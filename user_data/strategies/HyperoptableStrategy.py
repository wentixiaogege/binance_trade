from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, CategoricalParameter
from pandas import DataFrame
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib

class HyperoptableStrategy(IStrategy):
    """
    支持 Hyperopt 的策略
    定义可优化的参数范围
    """

    INTERFACE_VERSION = 3

    # ===== 可优化的参数 =====

    # 买入参数
    buy_rsi_threshold = IntParameter(20, 40, default=30, space='buy')
    buy_rsi_enabled = CategoricalParameter([True, False], default=True, space='buy')

    buy_ema_short = IntParameter(5, 20, default=9, space='buy')
    buy_ema_long = IntParameter(15, 50, default=21, space='buy')

    # 卖出参数
    sell_rsi_threshold = IntParameter(60, 80, default=70, space='sell')
    sell_rsi_enabled = CategoricalParameter([True, False], default=True, space='sell')

    # ROI 参数
    minimal_roi = {
        "0": 0.10,
        "30": 0.05,
        "60": 0.03,
        "120": 0.01
    }

    # 止损参数
    stoploss = -0.10

    # 追踪止损参数
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    timeframe = '5m'
    startup_candle_count: int = 50

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        计算指标
        """
        # EMA（使用可优化的周期）
        for val in self.buy_ema_short.range:
            dataframe[f'ema_short_{val}'] = ta.EMA(dataframe, timeperiod=val)

        for val in self.buy_ema_long.range:
            dataframe[f'ema_long_{val}'] = ta.EMA(dataframe, timeperiod=val)

        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # 成交量
        dataframe['volume_mean'] = dataframe['volume'].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        买入信号（使用优化后的参数）
        """
        conditions = []

        # 条件 1：EMA 金叉
        conditions.append(
            qtpylib.crossed_above(
                dataframe[f'ema_short_{self.buy_ema_short.value}'],
                dataframe[f'ema_long_{self.buy_ema_long.value}']
            )
        )

        # 条件 2：RSI（如果启用）
        if self.buy_rsi_enabled.value:
            conditions.append(dataframe['rsi'] > self.buy_rsi_threshold.value)
            conditions.append(dataframe['rsi'] < 70)

        # 条件 3：成交量
        conditions.append(dataframe['volume'] > dataframe['volume_mean'])

        # 确保有成交量
        conditions.append(dataframe['volume'] > 0)

        # 合并所有条件
        if conditions:
            dataframe.loc[
                reduce(lambda x, y: x & y, conditions),
                'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        卖出信号（使用优化后的参数）
        """
        conditions = []

        # 条件 1：EMA 死叉
        conditions.append(
            qtpylib.crossed_below(
                dataframe[f'ema_short_{self.buy_ema_short.value}'],
                dataframe[f'ema_long_{self.buy_ema_long.value}']
            )
        )

        # 条件 2：RSI（如果启用）
        if self.sell_rsi_enabled.value:
            conditions.append(dataframe['rsi'] > self.sell_rsi_threshold.value)

        # 确保有成交量
        conditions.append(dataframe['volume'] > 0)

        # 合并所有条件
        if conditions:
            dataframe.loc[
                reduce(lambda x, y: x & y, conditions),
                'exit_long'] = 1

        return dataframe