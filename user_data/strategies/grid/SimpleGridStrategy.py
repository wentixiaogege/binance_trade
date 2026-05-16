from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta

class SimpleGridStrategy(IStrategy):
    """
    简单网格交易策略
    在固定价格网格上低买高卖
    """

    INTERFACE_VERSION = 3

    # 关闭 ROI（网格策略自己控制卖出）
    minimal_roi = {
        "0": 100  # 很高的值，实际上不会触发
    }

    # 关闭全局止损（网格策略有自己的止损逻辑）
    stoploss = -0.99

    timeframe = '5m'
    startup_candle_count: int = 50

    # === 网格参数 ===
    # 网格间距（百分比）
    grid_spacing = 0.01  # 1%

    # 网格数量
    num_grids = 5

    # 中心价格（动态计算，使用最近 N 根 K 线的均价）
    center_price_period = 100

    # 是否启用动态中心价格
    dynamic_center = True

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        计算中心价格
        """
        # 动态中心价格（使用 SMA）
        dataframe['center_price'] = ta.SMA(dataframe['close'], timeperiod=self.center_price_period)

        # 计算价格与中心价格的偏离
        dataframe['price_deviation'] = (
            (dataframe['close'] - dataframe['center_price']) /
            dataframe['center_price'] * 100
        )

        # 标记当前处于哪个网格
        dataframe['grid_level'] = (
            dataframe['price_deviation'] / self.grid_spacing
        ).round()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        买入信号：价格低于中心价格的网格
        """
        dataframe.loc[
            (
                # 价格低于中心价格
                (dataframe['close'] < dataframe['center_price']) &

                # 至少偏离 1 个网格
                (dataframe['grid_level'] <= -1) &

                # 最多偏离 num_grids 个网格（避免过度下跌时买入）
                (dataframe['grid_level'] >= -self.num_grids) &

                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        卖出信号：价格高于买入价格 1 个网格以上
        """
        dataframe.loc[
            (
                # 价格高于中心价格
                (dataframe['close'] > dataframe['center_price']) &

                # 至少偏离 1 个网格
                (dataframe['grid_level'] >= 1) &

                (dataframe['volume'] > 0)
            ),
            'exit_long'] = 1

        return dataframe

    def custom_exit(self, pair: str, trade: 'Trade', current_time: 'datetime',
                    current_rate: float, current_profit: float, **kwargs) -> bool:
        """
        自定义卖出：达到 1 个网格间距就卖出
        """
        # 如果盈利达到网格间距，卖出
        if current_profit >= self.grid_spacing:
            return True

        return False