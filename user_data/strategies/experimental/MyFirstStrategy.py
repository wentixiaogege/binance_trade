from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta

class MyFirstStrategy(IStrategy):
    """
    我的第一个自定义策略
    """

    # 策略基本信息
    INTERFACE_VERSION = 3

    # 最小 ROI（可选，如果策略有自己的卖出逻辑可以设置很高）
    minimal_roi = {
        "0": 0.10,   # 10% 止盈
        "30": 0.05,  # 30 分钟后 5% 止盈
        "60": 0.03,  # 60 分钟后 3% 止盈
        "120": 0.01  # 120 分钟后 1% 止盈
    }

    # 止损
    stoploss = -0.03  # -3%

    # 追踪止损（可选）
    trailing_stop = False
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02

    # 时间框架
    timeframe = '5m'

    # 启动时下载的历史数据长度
    startup_candle_count: int = 100

    # 订单类型
    order_types = {
        'entry': 'limit',
        'exit': 'limit',
        'stoploss': 'market',
        'stoploss_on_exchange': True
    }

    # 订单超时
    order_time_in_force = {
        'entry': 'GTC',
        'exit': 'GTC'
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        添加技术指标
        这个函数在每次新数据到来时被调用
        """
        # 在这里添加你需要的指标
        # dataframe['indicator_name'] = ta.INDICATOR(dataframe)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        定义买入信号
        """
        dataframe.loc[
            (
                # 在这里添加你的买入条件
                # (dataframe['indicator'] > threshold) &
                (dataframe['volume'] > 0)  # 确保有成交量
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        定义卖出信号
        """
        dataframe.loc[
            (
                # 在这里添加你的卖出条件
                # (dataframe['indicator'] < threshold) &
                (dataframe['volume'] > 0)
            ),
            'exit_long'] = 1

        return dataframe