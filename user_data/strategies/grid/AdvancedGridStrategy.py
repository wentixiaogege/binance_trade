from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta
import numpy as np

class AdvancedGridStrategy(IStrategy):
    """
    高级网格策略
    - 动态调整网格间距（根据波动率）
    - 趋势过滤（避免单边市场）
    - 资金管理（限制最大持仓）
    """

    INTERFACE_VERSION = 3

    minimal_roi = {"0": 100}
    stoploss = -0.15  # 设置一个安全止损（防止极端情况）

    timeframe = '5m'
    startup_candle_count: int = 200

    # 最大同时持仓网格数
    max_open_grids = 3

    # 基础网格间距
    base_grid_spacing = 0.01  # 1%

    # 网格范围（上下各几个网格）
    grid_range = 5

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        计算指标
        """
        # 1. 中心价格（EMA）
        dataframe['center_price'] = ta.EMA(dataframe['close'], timeperiod=100)

        # 2. ATR（用于动态调整网格间距）
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['atr_pct'] = (dataframe['atr'] / dataframe['close']) * 100

        # 3. ADX（判断趋势强度，避免单边市场）
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)

        # 4. 动态网格间距（基于 ATR）
        dataframe['grid_spacing'] = dataframe['atr_pct'].clip(
            lower=self.base_grid_spacing,  # 最小 1%
            upper=self.base_grid_spacing * 3  # 最大 3%
        )

        # 5. 价格偏离百分比
        dataframe['price_deviation_pct'] = (
            (dataframe['close'] - dataframe['center_price']) /
            dataframe['center_price'] * 100
        )

        # 6. 当前网格层级
        dataframe['grid_level'] = (
            dataframe['price_deviation_pct'] / dataframe['grid_spacing']
        ).round()

        # 7. 成交量
        dataframe['volume_mean'] = dataframe['volume'].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        买入信号：
        1. 价格低于中心价格
        2. 无强趋势（ADX < 25，避免下跌趋势）
        3. 在网格范围内
        """
        dataframe.loc[
            (
                # 条件 1：价格低于中心价格
                (dataframe['close'] < dataframe['center_price']) &

                # 条件 2：趋势不要太强（避免单边市场）
                (dataframe['adx'] < 30) &

                # 条件 3：在买入网格范围内
                (dataframe['grid_level'] >= -self.grid_range) &
                (dataframe['grid_level'] <= -1) &

                # 条件 4：成交量正常
                (dataframe['volume'] > dataframe['volume_mean'] * 0.5) &

                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        卖出信号：价格回到中心价格以上
        """
        dataframe.loc[
            (
                # 价格高于中心价格
                (dataframe['close'] > dataframe['center_price']) &

                # 至少 1 个网格
                (dataframe['grid_level'] >= 1) &

                (dataframe['volume'] > 0)
            ),
            'exit_long'] = 1

        return dataframe

    def custom_stake_amount(self, pair: str, current_time: 'datetime',
                            current_rate: float, proposed_stake: float,
                            min_stake: float, max_stake: float, **kwargs) -> float:
        """
        自定义仓位：根据网格层级调整
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)

        if len(dataframe) == 0:
            return proposed_stake

        last_candle = dataframe.iloc[-1].squeeze()
        grid_level = last_candle['grid_level']

        # 越低的网格，仓位越大（但要控制总量）
        # 例如：
        # 网格 -1: 100% 仓位
        # 网格 -2: 120% 仓位
        # 网格 -3: 150% 仓位

        multiplier = 1.0 + (abs(grid_level) - 1) * 0.2
        multiplier = min(multiplier, 1.5)  # 最大 150%

        return proposed_stake * multiplier

    def custom_exit(self, pair: str, trade: 'Trade', current_time: 'datetime',
                    current_rate: float, current_profit: float, **kwargs) -> bool:
        """
        自定义卖出：达到 1 个网格间距即卖出
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)

        if len(dataframe) == 0:
            return False

        last_candle = dataframe.iloc[-1].squeeze()
        grid_spacing = last_candle['grid_spacing'] / 100  # 转为小数

        # 盈利超过当前网格间距，卖出
        if current_profit >= grid_spacing:
            return True

        return False