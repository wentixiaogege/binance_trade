# strategy_ghost_adaptive.py
import pandas as pd
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from pandas import DataFrame
from functools import reduce
from datetime import datetime, timedelta
import numpy as np
from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade

class GhostAdaptiveStrategy(IStrategy):
    """
    幽灵策略（自适应版）
    核心：极致回撤控制，动态止损 + 时间止损 + 市场状态感知
    """

    buy_params = {
        'ema_short': 20,
        'ema_long': 50,
        'rsi_oversold': 30,
        'adx_threshold': 25,
        'atr_multiplier_initial': 3.0,
        'atr_multiplier_trail': 2.0,
        'profit_lock': 0.02,
        'max_hold_hours': 24,
        'bear_stop_multiplier': 2.0,   # 熊市收紧止损
        'weekend_disable': False,
    }

    minimal_roi = {
        "0": 0.20,
        "60": 0.10,
        "120": 0.05,
        "240": 0
    }

    stoploss = -0.12
    timeframe = '5m'
    startup_candle_count = 200
    process_only_new_candles = False
    use_sell_signal = True
    sell_profit_only = False
    ignore_roi_if_buy_signal = False

    ANOMALY_DATES = [
        ('01-20', '02-10'),
        ('11-22', '11-28'),
        ('12-20', '01-05'),
    ]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['ema_short'] = ta.EMA(dataframe, timeperiod=self.buy_params['ema_short'])
        dataframe['ema_long'] = ta.EMA(dataframe, timeperiod=self.buy_params['ema_long'])
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)

        # 市场状态检测
        self._detect_market_phase(dataframe)
        return dataframe

    def _detect_market_phase(self, dataframe: DataFrame):
        ema200 = ta.EMA(dataframe['close'], timeperiod=200)
        price_vs_ema200 = (dataframe['close'] - ema200) / ema200
        dataframe['trend_bull'] = (price_vs_ema200 > 0.03) & (dataframe['adx'] > 25)
        dataframe['trend_bear'] = (price_vs_ema200 < -0.03) & (dataframe['adx'] > 25)
        dataframe['trend_ranging'] = ~(dataframe['trend_bull'] | dataframe['trend_bear'])

        volatility = dataframe['atr'] / dataframe['close']
        vol_ma = volatility.rolling(50).mean()
        dataframe['vol_high'] = volatility > vol_ma * 1.5
        dataframe['vol_low'] = volatility < vol_ma * 0.5

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            qtpylib.crossed_above(dataframe['ema_short'], dataframe['ema_long']),
            (dataframe['rsi'] > self.buy_params['rsi_oversold']),
            (dataframe['adx'] > self.buy_params['adx_threshold']),
        ]

        # 熊市减少入场
        if dataframe['trend_bear'].iloc[-1]:
            # 要求更严格的RSI
            conditions.append(dataframe['rsi'] > 40)

        if conditions:
            dataframe.loc[reduce(lambda x, y: x & y, conditions), 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            qtpylib.crossed_below(dataframe['ema_short'], dataframe['ema_long']),
        ]
        dataframe.loc[reduce(lambda x, y: x & y, conditions), 'exit_long'] = 1
        return dataframe

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return self.stoploss

        last_candle = dataframe.iloc[-1].squeeze()
        atr = last_candle.get('atr', 0)
        if atr <= 0:
            return self.stoploss

        # 基础初始止损
        initial_stop_distance = atr * self.buy_params['atr_multiplier_initial']
        initial_stop_price = trade.open_rate - initial_stop_distance
        initial_ratio = (initial_stop_price - trade.open_rate) / trade.open_rate

        stoploss_ratio = initial_ratio

        # 保本止损
        if current_profit > self.buy_params['profit_lock']:
            stoploss_ratio = max(stoploss_ratio, 0.0)

        # 追踪止损
        if current_profit > 0.05:
            trail_distance = atr * self.buy_params['atr_multiplier_trail']
            trail_price = current_rate - trail_distance
            trail_ratio = (trail_price - trade.open_rate) / trade.open_rate
            stoploss_ratio = max(stoploss_ratio, trail_ratio)

        # 根据市场状态调整
        if last_candle.get('trend_bear', False):
            stoploss_ratio = max(stoploss_ratio, -0.08)  # 熊市收紧最大止损
        if last_candle.get('vol_high', False):
            stoploss_ratio = max(stoploss_ratio, -0.10)  # 高波动收紧
        if self._is_weekend(current_time):
            stoploss_ratio = min(stoploss_ratio, -0.15)  # 周末放宽

        return max(stoploss_ratio, self.stoploss)

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            entry_tag: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake

        last_candle = dataframe.iloc[-1].squeeze()
        risk_factor = 1.0
        if last_candle.get('trend_bear', False):
            risk_factor *= 0.4
        if last_candle.get('vol_high', False):
            risk_factor *= 0.6
        if self._is_weekend(current_time):
            risk_factor *= 0.3
        if self._is_anomaly_month(current_time):
            risk_factor *= 0.2

        adjusted = proposed_stake * risk_factor
        return max(min_stake, min(adjusted, max_stake))

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        # 时间止损
        hold_time = (current_time - trade.open_date_utc).seconds / 3600
        if hold_time > self.buy_params['max_hold_hours']:
            return 'time_stop'
        # 周末强制平仓
        if self._is_weekend(current_time) and self.buy_params['weekend_disable']:
            return 'weekend_exit'
        # 异常日期强制平仓
        if self._is_anomaly_date(current_time):
            return 'anomaly_exit'
        return None

    def _is_weekend(self, current_time: datetime) -> bool:
        return current_time.weekday() >= 5

    def _is_anomaly_date(self, current_time: datetime) -> bool:
        date_str = current_time.strftime('%m-%d')
        for start, end in self.ANOMALY_DATES:
            if start <= date_str <= end:
                return True
        return False

    def _is_anomaly_month(self, current_time: datetime) -> bool:
        return current_time.month in [1, 12]