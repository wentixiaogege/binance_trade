# strategy_ghost_advanced.py
# 幽灵策略（改进版）- 极致回撤控制，多层动态止损 + 时间止损 + 波动率调整

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
from freqtrade.strategy import stoploss_from_open, DecimalParameter, IntParameter

class GhostAdvancedStrategy(IStrategy):
    """
    幽灵策略（改进版）
    核心：多层动态止损（初始、保本、追踪、波动率调整），辅以时间止损，入场采用稳健趋势信号。
    """

    buy_params = {
        "ema_short": 20,
        "ema_long": 50,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "adx_period": 14,
        "adx_threshold": 25,
        "atr_period": 14,
        "atr_multiplier_initial": 3.0,   # 初始止损倍数
        "atr_multiplier_trail": 2.0,      # 追踪止损倍数
        "profit_lock": 0.02,               # 锁定利润的阈值
        "max_hold_hours": 24,               # 最大持仓时间（小时）
    }

    sell_params = {}

    minimal_roi = {
        "0": 0.20,   # 大目标，主要依赖止损保护
        "60": 0.10,
        "120": 0.05,
        "240": 0
    }

    stoploss = -0.12  # 初始最大止损

    timeframe = '5m'
    startup_candle_count = 100

    process_only_new_candles = False

    # 不使用内置追踪止损，因为自定义止损更灵活
    trailing_stop = False

    use_sell_signal = True
    sell_profit_only = False
    ignore_roi_if_buy_signal = False

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 趋势指标
        dataframe['ema_short'] = ta.EMA(dataframe, timeperiod=self.buy_params['ema_short'])
        dataframe['ema_long'] = ta.EMA(dataframe, timeperiod=self.buy_params['ema_long'])

        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=self.buy_params['rsi_period'])

        # ADX
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=self.buy_params['adx_period'])

        # ATR
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=self.buy_params['atr_period'])

        # 波动率百分比（用于动态调整）
        dataframe['volatility'] = dataframe['atr'] / dataframe['close']

        # 成交量均线（可选）
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=20)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = []

        # 入场信号：EMA金叉 + RSI超卖反弹 + ADX趋势确认
        conditions.append(qtpylib.crossed_above(dataframe['ema_short'], dataframe['ema_long']))
        conditions.append(dataframe['rsi'] > self.buy_params['rsi_oversold'])
        conditions.append(dataframe['adx'] > self.buy_params['adx_threshold'])
        conditions.append(dataframe['close'] > dataframe['ema_short'])  # 价格在短期均线上方

        # 可选成交量放大确认
        # conditions.append(dataframe['volume'] > dataframe['volume_ma'])

        if conditions:
            dataframe.loc[reduce(lambda x, y: x & y, conditions), 'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 可以使用简单的趋势反转作为辅助退出信号
        conditions = [
            qtpylib.crossed_below(dataframe['ema_short'], dataframe['ema_long']),
        ]
        dataframe.loc[reduce(lambda x, y: x & y, conditions), 'exit_long'] = 1
        return dataframe

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """
        多层动态止损逻辑：
        1. 初始止损：基于开仓价的 ATR 倍数。
        2. 保本止损：当利润超过一定水平后，将止损移至开仓价。
        3. 追踪止损：利润继续增加时，使用更紧的 ATR 倍数跟随。
        4. 时间止损：持仓超过最大时间后，强制平仓（通过 custom_exit 实现）。
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return self.stoploss

        last_candle = dataframe.iloc[-1].squeeze()
        atr = last_candle.get('atr', 0)
        if atr <= 0:
            return self.stoploss

        # 初始止损距离（基于开仓价）
        initial_stop_distance = atr * self.buy_params['atr_multiplier_initial']
        initial_stop_price = trade.open_rate - initial_stop_distance
        initial_stop_ratio = (initial_stop_price - trade.open_rate) / trade.open_rate

        # 当前止损比例
        stoploss_ratio = initial_stop_ratio

        # 保本止损：如果利润超过 profit_lock，将止损移至开仓价
        if current_profit > self.buy_params['profit_lock']:
            break_even_stop_ratio = 0.0  # 保本
            stoploss_ratio = max(stoploss_ratio, break_even_stop_ratio)

        # 追踪止损：如果利润更高，使用更紧的追踪
        if current_profit > 0.05:
            trail_stop_distance = atr * self.buy_params['atr_multiplier_trail']
            trail_stop_price = current_rate - trail_stop_distance
            trail_stop_ratio = (trail_stop_price - trade.open_rate) / trade.open_rate
            stoploss_ratio = max(stoploss_ratio, trail_stop_ratio)

        # 返回负数止损比例，但不能高于初始最大止损
        return max(stoploss_ratio, self.stoploss)

    def custom_exit(self, pair: str, trade: 'Trade', current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        """
        时间止损：持仓超过最大小时数则强制退出。
        """
        if (current_time - trade.open_date_utc).seconds > self.buy_params['max_hold_hours'] * 3600:
            return 'time_stop'
        return None