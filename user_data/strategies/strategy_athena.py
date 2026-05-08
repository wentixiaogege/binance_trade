# strategy_athena.py
# 雅典娜策略 - 稳健趋势交易，多指标综合

from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
import pandas as pd
pd.options.mode.chained_assignment = None
import technical.indicators as ftt
from functools import reduce
import numpy as np

class AthenaStrategy(IStrategy):
    """
    雅典娜策略 (Athena)
    核心：稳健趋势策略，多指标综合
    指标：MACD、EMA均线系统、赫尔移动平均线 (HMA)
    入场：EMA多头排列 + MACD金叉 + 价格在HMA之上
    出场：MACD死叉 或 价格跌破HMA
    """

    buy_params = {
        "ema_short": 20,
        "ema_long": 50,
        "hma_period": 55,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
    }

    sell_params = {
        "ema_short": 20,
        "ema_long": 50,
        "hma_period": 55,
    }

    minimal_roi = {
        "0": 0.12,
        "30": 0.06,
        "60": 0.03,
        "120": 0
    }

    stoploss = -0.08

    timeframe = '5m'
    startup_candle_count = 100

    process_only_new_candles = False

    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    use_sell_signal = True
    sell_profit_only = False
    ignore_roi_if_buy_signal = False

    plot_config = {
        'main_plot': {
            'ema_short': {'color': 'blue'},
            'ema_long': {'color': 'red'},
            'hma': {'color': 'green'},
        },
        'subplots': {
            'MACD': {
                'macd': {'color': 'blue'},
                'macdsignal': {'color': 'orange'},
                'macdhist': {'color': 'green', 'type': 'bar'},
            }
        }
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # EMA 均线
        dataframe['ema_short'] = ta.EMA(dataframe, timeperiod=self.buy_params['ema_short'])
        dataframe['ema_long'] = ta.EMA(dataframe, timeperiod=self.buy_params['ema_long'])

        # 赫尔移动平均线 (HMA) - 手动实现
        # HMA = WMA(2*WMA(n/2) - WMA(n), sqrt(n))
        def hma(series, period):
            half_period = int(period / 2)
            sqrt_period = int(np.sqrt(period))
            wma_half = ta.WMA(series, timeperiod=half_period)
            wma_full = ta.WMA(series, timeperiod=period)
            hma_raw = 2 * wma_half - wma_full
            return ta.WMA(hma_raw, timeperiod=sqrt_period)

        dataframe['hma'] = hma(dataframe['close'], self.buy_params['hma_period'])

        # MACD
        macd = ta.MACD(dataframe,
                       fastperiod=self.buy_params['macd_fast'],
                       slowperiod=self.buy_params['macd_slow'],
                       signalperiod=self.buy_params['macd_signal'])
        dataframe['macd'] = macd['macd']
        dataframe['macdsignal'] = macd['macdsignal']
        dataframe['macdhist'] = macd['macdhist']

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            # 均线多头排列：短期 > 长期
            (dataframe['ema_short'] > dataframe['ema_long']),
            # 价格在 HMA 之上
            (dataframe['close'] > dataframe['hma']),
            # MACD 金叉
            qtpylib.crossed_above(dataframe['macd'], dataframe['macdsignal']),
            # 可选：MACD 柱状图由负转正
            # (dataframe['macdhist'] > 0),
        ]
        dataframe.loc[reduce(lambda x, y: x & y, conditions), 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            # MACD 死叉
            qtpylib.crossed_below(dataframe['macd'], dataframe['macdsignal']),
            # 或价格跌破 HMA（趋势反转）
            # qtpylib.crossed_below(dataframe['close'], dataframe['hma']),
        ]
        # 可选择两种条件之一或组合
        # 这里使用 MACD 死叉作为主要退出信号
        dataframe.loc[reduce(lambda x, y: x & y, conditions), 'exit_long'] = 1
        return dataframe