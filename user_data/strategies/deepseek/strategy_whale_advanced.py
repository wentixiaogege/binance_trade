# strategy_whale_advanced.py
# 鲸鱼策略（改进版）- 捕捉大资金吸筹/派发，集成资金流量、成交量分布、支撑阻力

from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
import pandas as pd
pd.options.mode.chained_assignment = None
import technical.indicators as ftt
from functools import reduce
from datetime import datetime, timedelta
import numpy as np
from freqtrade.strategy import merge_informative_pair

class WhaleAdvancedStrategy(IStrategy):
    """
    鲸鱼策略（改进版）
    核心：通过成交量分析、资金流量指标和价格位置识别主力吸筹/派发。
    """

    buy_params = {
        # 成交量指标参数
        "volume_ma_period": 20,
        "volume_spike_factor": 1.5,
        "obv_ma_short": 5,
        "obv_ma_long": 20,
        "mfi_period": 14,
        "mfi_oversold": 20,
        # 价格位置
        "lookback_period": 50,
        "price_low_percentile": 0.03,  # 价格在近期低点3%以内
        # 趋势过滤
        "ema_long": 200,
        # 吸筹确认
        "accumulation_days": 5,  # 连续几天成交量放大且价格不跌
        # 多时间框架
        "informative_timeframe": "1h",
        # 风险控制
        "atr_period": 14,
        "atr_stop_multiplier": 2.0,
    }

    sell_params = {
        "mfi_overbought": 80,
        "price_high_percentile": 0.97,
        "volume_shrink_factor": 0.7,
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

    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        informative_pairs = [(pair, self.buy_params['informative_timeframe']) for pair in pairs]
        return informative_pairs

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 合并更高时间框架数据
        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe=self.buy_params['informative_timeframe'])
        informative['ema_long'] = ta.EMA(informative, timeperiod=200)
        informative['volume_ma'] = ta.SMA(informative['volume'], timeperiod=20)
        informative = informative.rename(columns={
            'close': 'close_1h',
            'volume': 'volume_1h',
            'ema_long': 'ema_long_1h',
            'volume_ma': 'volume_ma_1h'
        })
        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, self.buy_params['informative_timeframe'], ffill=True)

        # 成交量分析
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=self.buy_params['volume_ma_period'])
        dataframe['volume_spike'] = dataframe['volume'] > (dataframe['volume_ma'] * self.buy_params['volume_spike_factor'])

        # 能量潮OBV及其均线
        dataframe['obv'] = ta.OBV(dataframe['close'], dataframe['volume'])
        dataframe['obv_ma_short'] = ta.SMA(dataframe['obv'], timeperiod=self.buy_params['obv_ma_short'])
        dataframe['obv_ma_long'] = ta.SMA(dataframe['obv'], timeperiod=self.buy_params['obv_ma_long'])

        # 资金流量指标MFI（结合价格和成交量）
        dataframe['mfi'] = ta.MFI(dataframe, timeperiod=self.buy_params['mfi_period'])

        # 累积/派发线ADL
        dataframe['adl'] = ta.AD(dataframe['high'], dataframe['low'], dataframe['close'], dataframe['volume'])

        # 价格位置：近期低点和高点
        dataframe['min_lookback'] = ta.MIN(dataframe['close'], timeperiod=self.buy_params['lookback_period'])
        dataframe['max_lookback'] = ta.MAX(dataframe['close'], timeperiod=self.buy_params['lookback_period'])

        # 接近低点/高点标志
        dataframe['near_low'] = (
            dataframe['close'] <= dataframe['min_lookback'] * (1 + self.buy_params['price_low_percentile'])
        )
        dataframe['near_high'] = (
            dataframe['close'] >= dataframe['max_lookback'] * self.sell_params['price_high_percentile']
        )

        # 吸筹模式：价格下跌但成交量放大且收盘价收高（长下影线）
        dataframe['bullish_engulfing'] = (
            (dataframe['open'] > dataframe['close'].shift(1)) &
            (dataframe['close'] < dataframe['open'].shift(1)) &
            (dataframe['close'] > dataframe['open'])
        )

        # 长期EMA趋势
        dataframe['ema_long'] = ta.EMA(dataframe['close'], timeperiod=self.buy_params['ema_long'])

        # ATR
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=self.buy_params['atr_period'])

        # 成交量萎缩标志（用于派发）
        dataframe['volume_shrink'] = dataframe['volume'] < (dataframe['volume_ma'] * self.sell_params['volume_shrink_factor'])

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = []

        # 基础条件：价格在长期EMA之上（上升趋势）
        conditions.append(dataframe['close'] > dataframe['ema_long'])

        # 价格处于近期低位（吸筹区域）
        conditions.append(dataframe['near_low'] == True)

        # OBV短期均线上穿长期均线（量能走强）
        conditions.append(qtpylib.crossed_above(dataframe['obv_ma_short'], dataframe['obv_ma_long']))

        # MFI超卖后回升（资金流入）
        conditions.append(dataframe['mfi'] > self.buy_params['mfi_oversold'])
        conditions.append(dataframe['mfi'] > dataframe['mfi'].shift(1))

        # 成交量放大确认
        conditions.append(dataframe['volume_spike'] == True)

        # 连续多日成交量放大且价格不跌（吸筹持续）
        accum_cond = True
        for i in range(1, self.buy_params['accumulation_days']):
            accum_cond &= (
                (dataframe['volume'].shift(i) > dataframe['volume_ma'].shift(i)) &
                (dataframe['close'].shift(i) >= dataframe['open'].shift(i))
            )
        conditions.append(accum_cond)

        # 更高时间框架也处于上升趋势（EMA>200）
        informative_ema_col = f"ema_long_{self.buy_params['informative_timeframe']}"
        conditions.append(dataframe[informative_ema_col] > 0)  # 确保数据存在，实际应判断价格在该EMA之上
        # 简化：假设close_1h > ema_long_1h
        conditions.append(dataframe['close_1h'] > dataframe[informative_ema_col])

        if conditions:
            dataframe.loc[reduce(lambda x, y: x & y, conditions), 'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = []

        # 派发信号：价格处于高位，OBV短期均线下穿长期均线，成交量萎缩
        cond1 = (
            (dataframe['near_high'] == True) &
            qtpylib.crossed_below(dataframe['obv_ma_short'], dataframe['obv_ma_long']) &
            (dataframe['volume_shrink'] == True)
        )
        conditions.append(cond1)

        # MFI超买后回落
        cond2 = (
            (dataframe['mfi'] > self.sell_params['mfi_overbought']) &
            (dataframe['mfi'] < dataframe['mfi'].shift(1))
        )
        conditions.append(cond2)

        # 价格跌破长期EMA
        cond3 = qtpylib.crossed_below(dataframe['close'], dataframe['ema_long'])
        conditions.append(cond3)

        # 更高时间框架转弱
        cond4 = qtpylib.crossed_below(dataframe['close_1h'], dataframe[f"ema_long_{self.buy_params['informative_timeframe']}"])
        conditions.append(cond4)

        if conditions:
            dataframe.loc[reduce(lambda x, y: x | y, conditions), 'exit_long'] = 1

        return dataframe