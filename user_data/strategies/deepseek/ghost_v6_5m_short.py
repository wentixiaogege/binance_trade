# strategy_ghost_advanced.py
# Ghost Advanced Strategy v6 - 5m做空版
# 核心：5m数据 + RSI超买做空
# 信号：RSI > 70 + BB上轨(pos > 0.95)
# 参数：SL=0.5% + TP=8% + Hold=48

from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
import talib.abstract as ta
import pandas as pd
pd.options.mode.chained_assignment = None
from datetime import datetime
from freqtrade.strategy import IntParameter, DecimalParameter

class GhostAdvancedStrategy(IStrategy):
    """
    Ghost Advanced v6 - 5m RSI超买做空
    数据：5m
    信号：RSI > 70 + BB上轨(pos > 0.95)
    止损：0.5%
    止盈：8%
    持仓：最多48根（约4小时）
    """

    minimal_roi = {"0": 100}
    stoploss = -0.005
    timeframe = '5m'
    startup_candle_count = 30
    process_only_new_candles = True
    trailing_stop = False
    use_exit_signal = False
    can_short = True

    # 参数
    buy_rsi = IntParameter(65, 80, default=70, space='buy', optimize=True)
    buy_bb_pos = DecimalParameter(0.90, 1.0, default=0.95, space='buy', optimize=True)
    sell_tp = DecimalParameter(0.05, 0.15, default=0.08, space='sell', optimize=True)
    max_hold = IntParameter(30, 100, default=48, space='sell', optimize=True)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        bb = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_upper'] = bb['upperband']
        dataframe['bb_lower'] = bb['lowerband']
        dataframe['bb_pos'] = (
            (dataframe['close'] - dataframe['bb_lower']) /
            (dataframe['bb_upper'] - dataframe['bb_lower'] + 1e-10)
        )
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 做空信号
        dataframe.loc[
            (dataframe['rsi'] > self.buy_rsi.value) &
            (dataframe['bb_pos'] > self.buy_bb_pos.value),
            'enter_short'
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(self, pair: str, trade: 'Trade', current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        if current_profit >= self.sell_tp.value:
            return 'tp_hit'
        hold_bars = (current_time - trade.open_date_utc).total_seconds() / 300  # 5m=300s
        if hold_bars >= self.max_hold.value:
            return 'time_up'
        return None

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, side: str,
                 **kwargs) -> float:
        return 1.0
