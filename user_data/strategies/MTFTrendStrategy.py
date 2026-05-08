from freqtrade.strategy import IStrategy, informative
from pandas import DataFrame
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib

class MTFTrendStrategy(IStrategy):
    """
    多时间框架趋势跟随策略
    1h 确认趋势 → 15m 等待回调 → 5m 金叉入场
    """

    INTERFACE_VERSION = 3

    minimal_roi = {
        "0": 0.10,
        "30": 0.05,
        "60": 0.03,
        "120": 0.01
    }

    stoploss = -0.03

    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    timeframe = '5m'
    startup_candle_count: int = 200

    # ========== 1 小时时间框架 ==========
    @informative('1h')
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        1 小时：判断大趋势
        """
        # 长期趋势均线
        dataframe['ema_50'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['ema_200'] = ta.EMA(dataframe, timeperiod=200)

        # 趋势强度
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)

        # MACD
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe['macd'] = macd['macd']
        dataframe['macdsignal'] = macd['macdsignal']

        return dataframe

    # ========== 15 分钟时间框架 ==========
    @informative('15m')
    def populate_indicators_15m(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        15 分钟：寻找回调机会
        """
        # 中期均线
        dataframe['ema_20'] = ta.EMA(dataframe, timeperiod=20)
        dataframe['ema_50'] = ta.EMA(dataframe, timeperiod=50)

        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # 布林带（判断回调幅度）
        bollinger = qtpylib.bollinger_bands(dataframe['close'], window=20, stds=2)
        dataframe['bb_lower'] = bollinger['lower']
        dataframe['bb_middle'] = bollinger['mid']

        return dataframe

    # ========== 5 分钟时间框架 ==========
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        5 分钟：精确入场
        """
        # 快速均线
        dataframe['ema_fast'] = ta.EMA(dataframe, timeperiod=9)
        dataframe['ema_slow'] = ta.EMA(dataframe, timeperiod=21)

        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # 成交量
        dataframe['volume_mean'] = dataframe['volume'].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        买入信号：三个时间框架共同确认
        """
        dataframe.loc[
            (
                # ===== 1 小时条件：确认上涨趋势 =====
                (dataframe['ema_50_1h'] > dataframe['ema_200_1h']) &  # 多头排列
                (dataframe['close'] > dataframe['ema_50_1h']) &       # 价格在趋势之上
                (dataframe['adx_1h'] > 25) &                          # 趋势明确
                (dataframe['macd_1h'] > dataframe['macdsignal_1h']) & # MACD 多头

                # ===== 15 分钟条件：回调到位 =====
                (dataframe['close'] > dataframe['ema_50_15m']) &      # 仍在中期趋势之上
                (dataframe['rsi_15m'] > 40) &                         # RSI 不要太弱
                (dataframe['rsi_15m'] < 60) &                         # 也不要太强（留空间）
                (dataframe['close'] < dataframe['ema_20_15m']) &      # 价格回调到 EMA 20 以下

                # ===== 5 分钟条件：金叉入场 =====
                (qtpylib.crossed_above(dataframe['ema_fast'], dataframe['ema_slow'])) &
                (dataframe['rsi'] > 45) &
                (dataframe['volume'] > dataframe['volume_mean']) &

                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        卖出信号：趋势反转
        """
        dataframe.loc[
            (
                # 1 小时趋势反转
                (
                    (dataframe['ema_50_1h'] < dataframe['ema_200_1h']) |  # 空头排列
                    (dataframe['macd_1h'] < dataframe['macdsignal_1h'])   # MACD 空头
                ) |

                # 或 5 分钟死叉
                (qtpylib.crossed_below(dataframe['ema_fast'], dataframe['ema_slow']))
            ) &
            (dataframe['volume'] > 0),
            'exit_long'] = 1

        return dataframe