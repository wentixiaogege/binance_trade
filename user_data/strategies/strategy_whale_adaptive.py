# strategy_whale_adaptive.py
import pandas as pd
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from pandas import DataFrame
from functools import reduce
from datetime import datetime
import numpy as np
from freqtrade.strategy import IStrategy, DecimalParameter, IntParameter
from freqtrade.persistence import Trade

class WhaleAdaptiveStrategy(IStrategy):
    """
    鲸鱼策略（自适应版）
    核心：捕捉大资金行为，集成市场状态检测，动态调整吸筹/派发识别阈值。
    """

    buy_params = {
        'obv_ma_short': 5,
        'obv_ma_long': 20,
        'volume_spike_factor': 1.5,
        'mfi_period': 14,
        'mfi_oversold': 20,
        'lookback_period': 50,
        'price_low_percentile': 0.03,
        'ema_long': 200,
        'accumulation_days': 5,
        'atr_stop_multiplier': 2.0,
        'bull_obv_factor': 1.2,       # 牛市放宽OBV要求
        'bear_volume_factor': 0.7,     # 熊市成交量要求更高
        'weekend_disable': False,
    }

    minimal_roi = {
        "0": 0.08,
        "30": 0.05,
        "60": 0.03,
        "120": 0.01,
        "240": 0
    }

    stoploss = -0.15
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
        # 基础指标
        dataframe['obv'] = ta.OBV(dataframe['close'], dataframe['volume'])
        dataframe['obv_ma_short'] = ta.SMA(dataframe['obv'], timeperiod=self.buy_params['obv_ma_short'])
        dataframe['obv_ma_long'] = ta.SMA(dataframe['obv'], timeperiod=self.buy_params['obv_ma_long'])
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=20)
        dataframe['volume_spike'] = dataframe['volume'] > (dataframe['volume_ma'] * self.buy_params['volume_spike_factor'])
        dataframe['mfi'] = ta.MFI(dataframe, timeperiod=self.buy_params['mfi_period'])
        dataframe['min_lookback'] = ta.MIN(dataframe['close'], timeperiod=self.buy_params['lookback_period'])
        dataframe['max_lookback'] = ta.MAX(dataframe['close'], timeperiod=self.buy_params['lookback_period'])
        dataframe['near_low'] = dataframe['close'] <= dataframe['min_lookback'] * (1 + self.buy_params['price_low_percentile'])
        dataframe['ema_long'] = ta.EMA(dataframe['close'], timeperiod=self.buy_params['ema_long'])
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)

        # 市场状态检测
        self._detect_market_phase(dataframe)
        return dataframe

    def _detect_market_phase(self, dataframe: DataFrame):
        ema200 = ta.EMA(dataframe['close'], timeperiod=200)
        price_vs_ema200 = (dataframe['close'] - ema200) / ema200
        adx = ta.ADX(dataframe, timeperiod=14)
        dataframe['trend_bull'] = (price_vs_ema200 > 0.03) & (adx > 25)
        dataframe['trend_bear'] = (price_vs_ema200 < -0.03) & (adx > 25)
        dataframe['trend_ranging'] = ~(dataframe['trend_bull'] | dataframe['trend_bear'])

        volatility = dataframe['atr'] / dataframe['close']
        vol_ma = volatility.rolling(50).mean()
        dataframe['vol_high'] = volatility > vol_ma * 1.5
        dataframe['vol_low'] = volatility < vol_ma * 0.5

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            (dataframe['close'] > dataframe['ema_long']),
            (dataframe['near_low'] == True),
            (dataframe['mfi'] > self.buy_params['mfi_oversold']),
            (dataframe['volume_spike'] == True),
        ]

        # OBV条件根据市场状态调整
        if dataframe['trend_bull'].iloc[-1]:
            # 牛市放宽OBV要求
            conditions.append(dataframe['obv_ma_short'] > dataframe['obv_ma_long'].shift(1))
        else:
            conditions.append(qtpylib.crossed_above(dataframe['obv_ma_short'], dataframe['obv_ma_long']))

        # 熊市增加成交量要求
        if dataframe['trend_bear'].iloc[-1]:
            conditions.append(dataframe['volume'] > dataframe['volume_ma'] * 2.0)  # 更高倍数

        # 连续吸筹条件
        accum_cond = True
        for i in range(1, self.buy_params['accumulation_days']):
            accum_cond &= (
                (dataframe['volume'].shift(i) > dataframe['volume_ma'].shift(i)) &
                (dataframe['close'].shift(i) >= dataframe['open'].shift(i))
            )
        conditions.append(accum_cond)

        if conditions:
            dataframe.loc[reduce(lambda x, y: x & y, conditions), 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            (dataframe['near_low'] == False) & qtpylib.crossed_below(dataframe['obv_ma_short'], dataframe['obv_ma_long']),
            qtpylib.crossed_below(dataframe['close'], dataframe['ema_long']),
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
        # 根据市场状态调整
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