from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib

class ScalpingStrategy(IStrategy):
    """
    剥头皮策略（Scalping）
    捕捉 1 分钟图上的快速波动
    目标：单笔 0.3-1% 的小利润，快进快出
    """

    INTERFACE_VERSION = 3

    # 快速止盈
    minimal_roi = {
        "0": 0.01,   # 1% 立即止盈
        "5": 0.008,  # 5 分钟后 0.8%
        "10": 0.005, # 10 分钟后 0.5%
        "15": 0.003  # 15 分钟后 0.3%
    }

    # 紧止损
    stoploss = -0.015  # -1.5%

    # 使用 1 分钟图
    timeframe = '1m'
    startup_candle_count: int = 30

    # 订单类型（市价单，快速成交）
    order_types = {
        'entry': 'market',  # 市价买入
        'exit': 'market',   # 市价卖出
        'stoploss': 'market',
        'stoploss_on_exchange': True
    }

    # 不允许持仓超过 30 分钟（强制平仓）
    max_holding_minutes = 30

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        使用快速指标
        """
        # 极短期 EMA
        dataframe['ema_5'] = ta.EMA(dataframe, timeperiod=5)
        dataframe['ema_10'] = ta.EMA(dataframe, timeperiod=10)

        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=7)  # 更短周期

        # 布林带（窄周期）
        bollinger = qtpylib.bollinger_bands(dataframe['close'], window=10, stds=2)
        dataframe['bb_lower'] = bollinger['lower']
        dataframe['bb_middle'] = bollinger['mid']
        dataframe['bb_upper'] = bollinger['upper']

        # 成交量突增
        dataframe['volume_mean'] = dataframe['volume'].rolling(window=10).mean()
        dataframe['volume_surge'] = dataframe['volume'] / dataframe['volume_mean']

        # 价格动量
        dataframe['price_momentum'] = (
            (dataframe['close'] - dataframe['close'].shift(3)) /
            dataframe['close'].shift(3) * 100
        )

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        买入信号：快速反弹
        """
        dataframe.loc[
            (
                # 条件 1：快速 EMA 金叉
                (qtpylib.crossed_above(dataframe['ema_5'], dataframe['ema_10'])) &

                # 条件 2：RSI 从超卖恢复
                (dataframe['rsi'] > 30) &
                (dataframe['rsi'] < 60) &
                (dataframe['rsi'] > dataframe['rsi'].shift(1)) &  # RSI 上升

                # 条件 3：价格在布林带下半部（有上涨空间）
                (dataframe['close'] < dataframe['bb_middle']) &

                # 条件 4：成交量突增（确认动能）
                (dataframe['volume_surge'] > 1.5) &

                # 条件 5：短期动量向上
                (dataframe['price_momentum'] > 0) &

                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        卖出信号：快速获利或反转
        """
        dataframe.loc[
            (
                (
                    # 条件 1：EMA 死叉
                    (qtpylib.crossed_below(dataframe['ema_5'], dataframe['ema_10'])) |

                    # 或条件 2：RSI 超买
                    (dataframe['rsi'] > 70) |

                    # 或条件 3：价格触及布林带上轨
                    (dataframe['close'] > dataframe['bb_upper'])
                ) &

                (dataframe['volume'] > 0)
            ),
            'exit_long'] = 1

        return dataframe

    def custom_exit(self, pair: str, trade: 'Trade', current_time: 'datetime',
                    current_rate: float, current_profit: float, **kwargs) -> bool:
        """
        强制平仓：超过最大持仓时间
        """
        # 计算持仓时间（分钟）
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 60

        # 超过 30 分钟，无论盈亏都平仓
        if trade_duration > self.max_holding_minutes:
            return True

        # 快速止盈：0.5% 就走
        if current_profit >= 0.005:
            return True

        return False