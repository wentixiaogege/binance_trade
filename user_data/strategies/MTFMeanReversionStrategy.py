from freqtrade.strategy import IStrategy, informative
from pandas import DataFrame
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib

class MTFMeanReversionStrategy(IStrategy):
    """
    多时间框架均值回归策略
    适用于震荡市场
    """

    INTERFACE_VERSION = 3

    minimal_roi = {
        "0": 0.05,
        "20": 0.03,
        "40": 0.02,
        "60": 0.01
    }

    stoploss = -0.04
    timeframe = '5m'
    startup_candle_count: int = 100

    # ========== 1 小时时间框架 ==========
    @informative('1h')
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        1 小时：判断市场状态（趋势 or 震荡）
        """
        # ADX 判断趋势强度
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)

        # EMA
        dataframe['ema_100'] = ta.EMA(dataframe, timeperiod=100)

        return dataframe

    # ========== 15 分钟时间框架 ==========
    @informative('15m')
    def populate_indicators_15m(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        15 分钟：判断偏离程度
        """
        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # 布林带
        bollinger = qtpylib.bollinger_bands(dataframe['close'], window=20, stds=2)
        dataframe['bb_lower'] = bollinger['lower']
        dataframe['bb_upper'] = bollinger['upper']
        dataframe['bb_middle'] = bollinger['mid']

        return dataframe

    # ========== 5 分钟时间框架 ==========
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        5 分钟：寻找反转信号
        """
        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # EMA
        dataframe['ema_20'] = ta.EMA(dataframe, timeperiod=20)

        # 成交量
        dataframe['volume_mean'] = dataframe['volume'].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        买入信号：震荡市中的超卖反弹
        """
        dataframe.loc[
            (
                # ===== 1 小时条件：震荡市 =====
                (dataframe['adx_1h'] < 25) &  # 无明确趋势

                # ===== 15 分钟条件：超卖 =====
                (dataframe['rsi_15m'] < 30) &  # RSI 超卖
                (dataframe['close'] < dataframe['bb_lower_15m']) &  # 价格低于布林带下轨

                # ===== 5 分钟条件：反转确认 =====
                (dataframe['rsi'] > dataframe['rsi'].shift(1)) &  # RSI 开始回升
                (dataframe['rsi'] > 30) &  # 脱离超卖区
                (dataframe['close'] > dataframe['ema_20']) &  # 价格回到均线之上
                (dataframe['volume'] > dataframe['volume_mean']) &

                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        卖出信号：回归完成
        """
        dataframe.loc[
            (
                # 15 分钟 RSI 回到中性区
                (dataframe['rsi_15m'] > 50) |

                # 或价格触及布林带中轨
                (dataframe['close'] > dataframe['bb_middle_15m'])
            ) &
            (dataframe['volume'] > 0),
            'exit_long'] = 1

        return dataframe