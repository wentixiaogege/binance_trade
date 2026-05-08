from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib

class SimpleCrossStrategy(IStrategy):
    """
    简单均线交叉策略
    EMA 9/21 金叉死叉 + RSI 过滤
    """

    INTERFACE_VERSION = 3

    # ROI 配置（相对保守）
    minimal_roi = {
        "0": 0.15,
        "30": 0.08,
        "60": 0.05,
        "120": 0.02
    }

    # 止损 -3%
    stoploss = -0.03

    # 追踪止损
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    # 时间框架
    timeframe = '5m'

    # 启动时加载 50 根 K 线（足够计算 EMA 21）
    startup_candle_count: int = 50

    # 订单配置
    order_types = {
        'entry': 'limit',
        'exit': 'limit',
        'stoploss': 'market',
        'stoploss_on_exchange': True
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        添加技术指标
        """
        # 1. 计算 EMA
        dataframe['ema_fast'] = ta.EMA(dataframe, timeperiod=9)
        dataframe['ema_slow'] = ta.EMA(dataframe, timeperiod=21)

        # 2. 计算 RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # 3. 计算平均成交量
        dataframe['volume_mean'] = dataframe['volume'].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        买入信号：EMA 金叉 + RSI 过滤
        """
        dataframe.loc[
            (
                # 条件 1：金叉（快线上穿慢线）
                (qtpylib.crossed_above(dataframe['ema_fast'], dataframe['ema_slow'])) &

                # 条件 2：RSI 不在超卖区（避免买在底部反弹）
                (dataframe['rsi'] > 30) &
                (dataframe['rsi'] < 70) &

                # 条件 3：成交量确认
                (dataframe['volume'] > dataframe['volume_mean']) &

                # 确保有成交量
                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        卖出信号：EMA 死叉或 RSI 超买
        """
        dataframe.loc[
            (
                (
                    # 条件 1：死叉（快线下穿慢线）
                    (qtpylib.crossed_below(dataframe['ema_fast'], dataframe['ema_slow'])) |

                    # 或条件 2：RSI 超买
                    (dataframe['rsi'] > 70)
                ) &

                # 确保有成交量
                (dataframe['volume'] > 0)
            ),
            'exit_long'] = 1

        return dataframe