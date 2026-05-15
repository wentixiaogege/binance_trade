# strategy_whale.py
# 鲸鱼策略 - 捕捉大资金吸筹与派发

from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
import pandas as pd
pd.options.mode.chained_assignment = None
import technical.indicators as ftt
from functools import reduce
import numpy as np

class WhaleStrategy(IStrategy):
    """
    鲸鱼策略 (Whale)
    核心：捕捉大资金行为，识别吸筹/派发
    指标：OBV、成交量突变、价格相对位置
    入场：价格处于低位 + OBV走强 + 成交量放大
    出场：价格处于高位 + OBV走弱 + 成交量萎缩
    """

    buy_params = {
        "obv_ma_short": 5,
        "obv_ma_long": 20,
        "volume_ma_period": 20,
        "volume_spike_factor": 1.5,
        "price_low_percentile": 0.02,  # 价格接近近期最低点的阈值（2%内）
        "lookback_period": 20,          # 近期低点回溯周期
    }

    sell_params = {
        "price_high_percentile": 0.98,  # 价格接近近期最高点的阈值
        "obv_ma_short": 5,
        "obv_ma_long": 20,
    }

    minimal_roi = {
        "0": 0.10,
        "30": 0.05,
        "60": 0.02,
        "120": 0
    }

    stoploss = -0.12

    timeframe = '5m'
    startup_candle_count = 50

    process_only_new_candles = False

    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    use_sell_signal = True
    sell_profit_only = False
    ignore_roi_if_buy_signal = False

    plot_config = {
        'main_plot': {},
        'subplots': {
            'OBV': {
                'obv': {},
                'obv_ma_short': {},
                'obv_ma_long': {},
            },
            'Volume': {
                'volume': {},
                'volume_ma': {},
            }
        }
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 能量潮 OBV
        dataframe['obv'] = ta.OBV(dataframe['close'], dataframe['volume'])
        dataframe['obv_ma_short'] = ta.SMA(dataframe['obv'], timeperiod=self.buy_params['obv_ma_short'])
        dataframe['obv_ma_long'] = ta.SMA(dataframe['obv'], timeperiod=self.buy_params['obv_ma_long'])

        # 成交量均线
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=self.buy_params['volume_ma_period'])

        # 价格相对位置：近期低点和高点
        dataframe['min_lookback'] = ta.MIN(dataframe['close'], timeperiod=self.buy_params['lookback_period'])
        dataframe['max_lookback'] = ta.MAX(dataframe['close'], timeperiod=self.buy_params['lookback_period'])

        # 价格接近低点标志
        dataframe['near_low'] = (
            dataframe['close'] <= dataframe['min_lookback'] * (1 + self.buy_params['price_low_percentile'])
        )

        # 价格接近高点标志
        dataframe['near_high'] = (
            dataframe['close'] >= dataframe['max_lookback'] * self.sell_params['price_high_percentile']
        )

        # 成交量突变
        dataframe['volume_spike'] = dataframe['volume'] > (dataframe['volume_ma'] * self.buy_params['volume_spike_factor'])

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 吸筹条件：价格在低位，OBV短期均线上穿长期均线（量能先行），成交量放大
        conditions = [
            (dataframe['near_low'] == True),
            qtpylib.crossed_above(dataframe['obv_ma_short'], dataframe['obv_ma_long']),
            (dataframe['volume_spike'] == True),
        ]
        dataframe.loc[reduce(lambda x, y: x & y, conditions), 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 派发条件：价格在高位，OBV短期均线下穿长期均线，成交量缩小（可选）
        conditions = [
            (dataframe['near_high'] == True),
            qtpylib.crossed_below(dataframe['obv_ma_short'], dataframe['obv_ma_long']),
        ]
        # 可附加成交量缩小条件：dataframe['volume'] < dataframe['volume_ma']
        dataframe.loc[reduce(lambda x, y: x & y, conditions), 'exit_long'] = 1
        return dataframe