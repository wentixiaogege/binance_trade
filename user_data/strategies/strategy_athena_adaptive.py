# strategy_athena_adaptive.py
import pandas as pd
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from pandas import DataFrame
from functools import reduce
from datetime import datetime
import numpy as np
from freqtrade.strategy import IStrategy
from freqtrade.persistence import Trade

class AthenaAdaptiveStrategy(IStrategy):
    """
    雅典娜策略（自适应版）
    核心：稳健趋势交易，多指标综合，根据市场状态调整趋势过滤和仓位。
    """

    buy_params = {
        'ema_short': 20,
        'ema_long': 50,
        'macd_fast': 12,
        'macd_slow': 26,
        'macd_signal': 9,
        'adx_threshold': 25,
        'hma_period': 55,
        'pullback_threshold': 0.02,
        'atr_stop_multiplier': 2.5,
        'bull_ema_factor': 1.1,   # 牛市放宽均线要求
        'bear_pullback': 0.01,     # 熊市回调要求更严格
        'weekend_disable': False,
    }

    minimal_roi = {
        "0": 0.10,
        "30": 0.06,
        "60": 0.04,
        "120": 0.02,
        "240": 0
    }

    stoploss = -0.10
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
        macd = ta.MACD(dataframe,
                        fastperiod=self.buy_params['macd_fast'],
                        slowperiod=self.buy_params['macd_slow'],
                        signalperiod=self.buy_params['macd_signal'])
        dataframe['macd'] = macd['macd']
        dataframe['macdsignal'] = macd['macdsignal']
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['plus_di'] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe['minus_di'] = ta.MINUS_DI(dataframe, timeperiod=14)

        # HMA
        def hma(series, period):
            half = int(period / 2)
            sqrt = int(np.sqrt(period))
            wma_half = ta.WMA(series, timeperiod=half)
            wma_full = ta.WMA(series, timeperiod=period)
            hma_raw = 2 * wma_half - wma_full
            return ta.WMA(hma_raw, timeperiod=sqrt)

        dataframe['hma'] = hma(dataframe['close'], self.buy_params['hma_period'])

        dataframe['recent_high'] = ta.MAX(dataframe['high'], timeperiod=20)
        dataframe['pullback'] = (dataframe['recent_high'] - dataframe['close']) / dataframe['recent_high']
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
            (dataframe['ema_short'] > dataframe['ema_long']),
            (dataframe['close'] > dataframe['hma']),
            qtpylib.crossed_above(dataframe['macd'], dataframe['macdsignal']),
            (dataframe['plus_di'] > dataframe['minus_di']),
            (dataframe['adx'] > self.buy_params['adx_threshold']),
            (dataframe['pullback'] >= self.buy_params['pullback_threshold']),
            (dataframe['pullback'] <= 0.05),
        ]

        # 根据市场状态调整
        if dataframe['trend_bull'].iloc[-1]:
            # 牛市放宽条件：允许稍弱的均线
            conditions.append(dataframe['ema_short'] > dataframe['ema_long'] * 0.98)  # 几乎不变
        if dataframe['trend_bear'].iloc[-1]:
            # 熊市要求更严格的回调
            conditions.append(dataframe['pullback'] <= 0.03)
            # 同时要求更高的ADX
            conditions.append(dataframe['adx'] > 30)

        if conditions:
            dataframe.loc[reduce(lambda x, y: x & y, conditions), 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            qtpylib.crossed_below(dataframe['macd'], dataframe['macdsignal']),
            qtpylib.crossed_below(dataframe['close'], dataframe['hma']),
            qtpylib.crossed_below(dataframe['plus_di'], dataframe['minus_di']),
        ]
        dataframe.loc[reduce(lambda x, y: x | y, conditions), 'exit_long'] = 1
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

        multiplier = self.buy_params['atr_stop_multiplier']
        if last_candle.get('trend_bear', False):
            multiplier *= 0.7
        if last_candle.get('vol_high', False):
            multiplier *= 0.8
        if self._is_weekend(current_time):
            multiplier *= 1.2

        stoploss_price = current_rate - atr * multiplier
        stoploss_ratio = (stoploss_price - trade.open_rate) / trade.open_rate
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
            risk_factor *= 0.5
        if last_candle.get('vol_high', False):
            risk_factor *= 0.7
        if self._is_weekend(current_time):
            risk_factor *= 0.5
        if self._is_anomaly_month(current_time):
            risk_factor *= 0.3

        adjusted = proposed_stake * risk_factor
        return max(min_stake, min(adjusted, max_stake))

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        if self._is_weekend(current_time) and self.buy_params['weekend_disable']:
            return 'weekend_exit'
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