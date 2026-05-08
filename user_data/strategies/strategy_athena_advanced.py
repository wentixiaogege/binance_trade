# strategy_athena_advanced.py
# 雅典娜策略（改进版）- 稳健趋势交易，多指标综合，自适应均线，多时间框架确认

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

class AthenaAdvancedStrategy(IStrategy):
    """
    雅典娜策略（改进版）
    核心：综合EMA、MACD、DMI、HMA等多种趋势指标，多时间框架确认，回调入场，趋势强度过滤。
    """

    buy_params = {
        # EMA
        "ema_short": 20,
        "ema_long": 50,
        # MACD
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        # DMI
        "adx_period": 14,
        "adx_threshold": 25,
        "di_plus_period": 14,
        "di_minus_period": 14,
        # HMA
        "hma_period": 55,
        # 回调入场
        "pullback_threshold": 0.02,  # 回调幅度（相对于最近高点）
        # 多时间框架
        "informative_timeframe": "1h",
        # 止损
        "atr_period": 14,
        "atr_stop_multiplier": 2.5,
    }

    sell_params = {
        "ema_long": 50,
        "adx_threshold": 20,
        "atr_exit_multiplier": 3.0,
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

    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    use_sell_signal = True
    sell_profit_only = False
    ignore_roi_if_buy_signal = False

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        informative_pairs = [(pair, self.buy_params['informative_timeframe']) for pair in pairs]
        return informative_pairs

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 合并更高时间框架数据
        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe=self.buy_params['informative_timeframe'])
        informative['ema_long'] = ta.EMA(informative, timeperiod=200)
        informative['adx'] = ta.ADX(informative, timeperiod=self.buy_params['adx_period'])
        informative = informative.rename(columns={
            'close': 'close_1h',
            'high': 'high_1h',
            'low': 'low_1h',
            'ema_long': 'ema_long_1h',
            'adx': 'adx_1h'
        })
        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, self.buy_params['informative_timeframe'], ffill=True)

        # EMA
        dataframe['ema_short'] = ta.EMA(dataframe, timeperiod=self.buy_params['ema_short'])
        dataframe['ema_long'] = ta.EMA(dataframe, timeperiod=self.buy_params['ema_long'])

        # MACD
        macd = ta.MACD(dataframe,
                       fastperiod=self.buy_params['macd_fast'],
                       slowperiod=self.buy_params['macd_slow'],
                       signalperiod=self.buy_params['macd_signal'])
        dataframe['macd'] = macd['macd']
        dataframe['macdsignal'] = macd['macdsignal']
        dataframe['macdhist'] = macd['macdhist']

        # DMI (ADX, DI+, DI-)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=self.buy_params['adx_period'])
        dataframe['plus_di'] = ta.PLUS_DI(dataframe, timeperiod=self.buy_params['di_plus_period'])
        dataframe['minus_di'] = ta.MINUS_DI(dataframe, timeperiod=self.buy_params['di_minus_period'])

        # 赫尔移动平均线 HMA
        def hma(series, period):
            half_period = int(period / 2)
            sqrt_period = int(np.sqrt(period))
            wma_half = ta.WMA(series, timeperiod=half_period)
            wma_full = ta.WMA(series, timeperiod=period)
            hma_raw = 2 * wma_half - wma_full
            return ta.WMA(hma_raw, timeperiod=sqrt_period)

        dataframe['hma'] = hma(dataframe['close'], self.buy_params['hma_period'])

        # 回调检测：价格从近期高点回落的幅度
        dataframe['recent_high'] = ta.MAX(dataframe['high'], timeperiod=20)
        dataframe['pullback'] = (dataframe['recent_high'] - dataframe['close']) / dataframe['recent_high']

        # ATR
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=self.buy_params['atr_period'])

        # 趋势强度：+DI > -DI 且 ADX > 阈值
        dataframe['trend_up'] = (dataframe['plus_di'] > dataframe['minus_di']) & (dataframe['adx'] > self.buy_params['adx_threshold'])

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = []

        # 1. 主要趋势向上：EMA短期 > 长期
        conditions.append(dataframe['ema_short'] > dataframe['ema_long'])

        # 2. 价格在 HMA 之上
        conditions.append(dataframe['close'] > dataframe['hma'])

        # 3. MACD 金叉（动能向上）
        conditions.append(qtpylib.crossed_above(dataframe['macd'], dataframe['macdsignal']))

        # 4. DMI 确认：+DI > -DI 且 ADX 足够大（趋势强度）
        conditions.append(dataframe['trend_up'] == True)

        # 5. 回调入场：价格从近期高点回落一定幅度，但未跌破 HMA
        conditions.append(dataframe['pullback'] >= self.buy_params['pullback_threshold'])
        conditions.append(dataframe['pullback'] <= 0.05)  # 回调不能太深，避免趋势反转

        # 6. 成交量温和放大（可选）
        # conditions.append(dataframe['volume'] > ta.SMA(dataframe['volume'], timeperiod=20))

        # 7. 更高时间框架也处于上升趋势（ADX > 25，且价格在 EMA 之上）
        conditions.append(dataframe['adx_1h'] > self.buy_params['adx_threshold'])
        conditions.append(dataframe['close_1h'] > dataframe['ema_long_1h'])

        if conditions:
            dataframe.loc[reduce(lambda x, y: x & y, conditions), 'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = []

        # 1. MACD 死叉
        cond1 = qtpylib.crossed_below(dataframe['macd'], dataframe['macdsignal'])
        conditions.append(cond1)

        # 2. 价格跌破 HMA
        cond2 = qtpylib.crossed_below(dataframe['close'], dataframe['hma'])
        conditions.append(cond2)

        # 3. DMI 转弱：-DI 上穿 +DI 或 ADX 跌破阈值
        cond3 = qtpylib.crossed_above(dataframe['minus_di'], dataframe['plus_di'])
        cond4 = dataframe['adx'] < self.sell_params['adx_threshold']
        conditions.append(cond3 | cond4)

        # 4. 价格跌破 EMA_long
        cond5 = qtpylib.crossed_below(dataframe['close'], dataframe['ema_long'])
        conditions.append(cond5)

        if conditions:
            dataframe.loc[reduce(lambda x, y: x | y, conditions), 'exit_long'] = 1

        return dataframe