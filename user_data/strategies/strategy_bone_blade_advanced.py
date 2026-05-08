# strategy_bone_blade_advanced.py
# 骨刃策略（改进版）- 高频波段交易，集成多时间框架、背离检测、动态止损

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
from freqtrade.strategy import merge_informative_pair, stoploss_from_open

class BoneBladeAdvancedStrategy(IStrategy):
    """
    骨刃策略（改进版）
    核心：高灵敏度短时框架波段交易，增加多时间框架确认、RSI背离、成交量验证和趋势过滤。
    """

    # 可调参数
    buy_params = {
        # 基础指标参数
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "volume_ma_period": 20,
        "volume_spike_factor": 1.8,
        # 趋势过滤
        "ema_short": 50,
        "ema_long": 200,
        # ADX过滤
        "adx_period": 14,
        "adx_threshold": 25,
        # 背离检测
        "rsi_div_lookback": 30,
        # 多时间框架
        "informative_timeframe": "15m",
        # 止损参数
        "atr_period": 14,
        "atr_multiplier": 2.0,
    }

    sell_params = {
        "rsi_overbought": 70,
        "use_quick_exit": True,
        "atr_multiplier_exit": 1.5,
    }

    # ROI 表（激进止盈，但由止损控制风险）
    minimal_roi = {
        "0": 0.05,
        "5": 0.03,
        "15": 0.02,
        "30": 0.01,
        "60": 0
    }

    stoploss = -0.10  # 初始止损 -10%

    timeframe = '5m'
    startup_candle_count = 200  # 保证EMA200可用

    process_only_new_candles = False

    # 追踪止损（与自定义止损二选一，这里使用自定义止损更灵活）
    trailing_stop = False

    use_sell_signal = True
    sell_profit_only = False
    ignore_roi_if_buy_signal = False

    # 合并信息对
    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        informative_pairs = [(pair, self.buy_params['informative_timeframe']) for pair in pairs]
        return informative_pairs

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 获取更高时间框架数据并合并
        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe=self.buy_params['informative_timeframe'])
        informative['rsi'] = ta.RSI(informative, timeperiod=self.buy_params['rsi_period'])
        informative['ema_short'] = ta.EMA(informative, timeperiod=50)  # 用于趋势判断
        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, self.buy_params['informative_timeframe'], ffill=True)

        # 基础指标
        # 布林带
        bollinger = ta.BBANDS(dataframe, timeperiod=self.buy_params['bb_period'],
                               nbdevup=self.buy_params['bb_std'], nbdevdn=self.buy_params['bb_std'])
        dataframe['bb_upper'] = bollinger['upperband']
        dataframe['bb_middle'] = bollinger['middleband']
        dataframe['bb_lower'] = bollinger['lowerband']
        dataframe['bb_width'] = (dataframe['bb_upper'] - dataframe['bb_lower']) / dataframe['bb_middle']
        dataframe['bb_position'] = (dataframe['close'] - dataframe['bb_lower']) / (dataframe['bb_upper'] - dataframe['bb_lower'])

        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=self.buy_params['rsi_period'])

        # RSI背离检测
        dataframe['rsi_high'] = ta.MAX(dataframe['rsi'], timeperiod=self.buy_params['rsi_div_lookback'])
        dataframe['rsi_low'] = ta.MIN(dataframe['rsi'], timeperiod=self.buy_params['rsi_div_lookback'])
        dataframe['price_high'] = ta.MAX(dataframe['high'], timeperiod=self.buy_params['rsi_div_lookback'])
        dataframe['price_low'] = ta.MIN(dataframe['low'], timeperiod=self.buy_params['rsi_div_lookback'])

        # 成交量均线和突变
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=self.buy_params['volume_ma_period'])
        dataframe['volume_spike'] = dataframe['volume'] > (dataframe['volume_ma'] * self.buy_params['volume_spike_factor'])

        # EMA趋势过滤
        dataframe['ema_short'] = ta.EMA(dataframe['close'], timeperiod=self.buy_params['ema_short'])
        dataframe['ema_long'] = ta.EMA(dataframe['close'], timeperiod=self.buy_params['ema_long'])

        # ADX趋势强度
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=self.buy_params['adx_period'])

        # ATR波动率
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=self.buy_params['atr_period'])

        # 价格形态：长下影线（锤子线）可能代表拒绝低价
        dataframe['lower_shadow'] = (dataframe['low'] < dataframe['open']) & (dataframe['close'] > dataframe['open'])

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = []

        # 1. 价格触及或跌破下轨
        conditions.append(dataframe['close'] <= dataframe['bb_lower'])

        # 2. RSI超卖
        conditions.append(dataframe['rsi'] < self.buy_params['rsi_oversold'])

        # 3. 成交量放大
        conditions.append(dataframe['volume_spike'] == True)

        # 4. 趋势过滤：价格在EMA_short之上，且EMA_short > EMA_long（上升趋势）
        conditions.append(dataframe['close'] > dataframe['ema_short'])
        conditions.append(dataframe['ema_short'] > dataframe['ema_long'])

        # 5. ADX大于阈值（确保有趋势，避免震荡）
        conditions.append(dataframe['adx'] > self.buy_params['adx_threshold'])

        # 6. RSI底背离检测（可选，增加信号可靠性）
        # 价格创新低但RSI未创新低
        rsi_div_cond = (
            (dataframe['low'] == dataframe['price_low']) &
            (dataframe['rsi'] > dataframe['rsi_low'].shift(1))
        )
        conditions.append(rsi_div_cond)

        # 7. 布林带收口后扩张（暗示突破）
        bb_break_cond = (
            (dataframe['bb_width'] > dataframe['bb_width'].shift(1)) &
            (dataframe['bb_width'].shift(1) < dataframe['bb_width'].shift(2))
        )
        conditions.append(bb_break_cond)

        # 8. 更高时间框架RSI也处于低位（避免逆大势）
        informative_rsi_col = f"rsi_{self.buy_params['informative_timeframe']}"
        conditions.append(dataframe[informative_rsi_col] < self.buy_params['rsi_oversold'])

        # 组合所有条件
        if conditions:
            dataframe.loc[reduce(lambda x, y: x & y, conditions), 'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = []

        # 1. 价格触及上轨且RSI超买
        cond1 = (dataframe['close'] >= dataframe['bb_upper']) & (dataframe['rsi'] > self.sell_params['rsi_overbought'])
        conditions.append(cond1)

        # 2. 快速止盈：价格从入场点上涨一定幅度（可用动态止盈，此处简单用布林带位置）
        if self.sell_params['use_quick_exit']:
            cond2 = (dataframe['bb_position'] > 0.8) & (dataframe['volume'] < dataframe['volume_ma'])
            conditions.append(cond2)

        # 3. 趋势反转：价格跌破EMA_short
        cond3 = qtpylib.crossed_below(dataframe['close'], dataframe['ema_short'])
        conditions.append(cond3)

        # 4. RSI顶背离（价格创新高但RSI未创新高）作为退出信号
        rsi_div_exit = (
            (dataframe['high'] == dataframe['price_high']) &
            (dataframe['rsi'] < dataframe['rsi_high'].shift(1))
        )
        conditions.append(rsi_div_exit)

        # 合并任意一个条件触发退出
        if conditions:
            dataframe.loc[reduce(lambda x, y: x | y, conditions), 'exit_long'] = 1

        return dataframe

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """
        动态止损：基于ATR的移动止损
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) > 0:
            last_candle = dataframe.iloc[-1].squeeze()
            atr = last_candle.get('atr', 0)
            if atr > 0 and current_rate > 0:
                # 止损价 = 最高价 - atr * multiplier
                # 这里使用移动止损：当利润超过一定水平后，收紧止损
                if current_profit > 0.02:
                    # 盈利后使用2倍ATR追踪
                    stoploss_price = current_rate - (atr * self.buy_params['atr_multiplier'])
                else:
                    # 初始止损基于开仓价
                    stoploss_price = trade.open_rate - (atr * self.buy_params['atr_multiplier'])

                stoploss_ratio = (stoploss_price - trade.open_rate) / trade.open_rate
                return max(stoploss_ratio, self.stoploss)  # 不能比初始止损更宽松
        return self.stoploss