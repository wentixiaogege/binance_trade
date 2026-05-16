from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.freqai.data_kitchen import FreqaiDataKitchen

class FreqAIStrategy(IStrategy):
    """
    使用 FreqAI 的简单策略
    预测价格方向，辅助交易决策
    """

    INTERFACE_VERSION = 3

    minimal_roi = {"0": 0.10}
    stoploss = -0.05
    timeframe = '5m'
    startup_candle_count = 100

    # FreqAI 配置
    process_only_new_candles = True

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        添加基础指标
        """
        # 这些指标会被 FreqAI 用作特征
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['ema_5'] = ta.EMA(dataframe, timeperiod=5)
        dataframe['ema_10'] = ta.EMA(dataframe, timeperiod=10)
        dataframe['ema_20'] = ta.EMA(dataframe, timeperiod=20)

        macd = ta.MACD(dataframe)
        dataframe['macd'] = macd['macd']
        dataframe['macdsignal'] = macd['macdsignal']

        return dataframe

    def feature_engineering_expand_all(self, dataframe: DataFrame, period: int,
                                       metadata: dict, **kwargs) -> DataFrame:
        """
        特征工程：创建用于 ML 的特征
        """
        # 价格变化
        dataframe[f'%-price_change_{period}'] = (
            dataframe['close'].pct_change(period) * 100
        )

        # RSI 变化
        dataframe[f'%-rsi_change_{period}'] = dataframe['rsi'].diff(period)

        # EMA 距离
        dataframe[f'%-ema_dist_{period}'] = (
            (dataframe['close'] - dataframe['ema_20']) /
            dataframe['ema_20'] * 100
        )

        # 成交量变化
        dataframe[f'%-volume_change_{period}'] = (
            dataframe['volume'].pct_change(period) * 100
        )

        return dataframe

    def feature_engineering_expand_basic(self, dataframe: DataFrame,
                                         metadata: dict, **kwargs) -> DataFrame:
        """
        基础特征
        """
        # 当前 RSI
        dataframe['%-rsi'] = dataframe['rsi']

        # MACD 差值
        dataframe['%-macd_diff'] = dataframe['macd'] - dataframe['macdsignal']

        return dataframe

    def feature_engineering_standard(self, dataframe: DataFrame,
                                     metadata: dict, **kwargs) -> DataFrame:
        """
        标准化特征
        """
        # 相对价格位置（0-1 之间）
        dataframe['%-price_position'] = (
            (dataframe['close'] - dataframe['low'].rolling(50).min()) /
            (dataframe['high'].rolling(50).max() - dataframe['low'].rolling(50).min())
        )

        return dataframe

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        """
        设置预测目标（监督学习的标签）
        """
        # 预测未来 3 根 K 线的价格方向
        # 1 = 上涨，0 = 下跌
        dataframe['&s-up_or_down'] = (
            dataframe['close'].shift(-3) > dataframe['close']
        ).astype(int)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        买入信号：基于 ML 预测
        """
        dataframe.loc[
            (
                # ML 预测上涨
                (dataframe['&s-up_or_down'] == 1) &

                # 预测置信度高
                (dataframe['do_predict'] == 1) &

                # RSI 不在超买区
                (dataframe['rsi'] < 70) &

                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        卖出信号：基于 ML 预测
        """
        dataframe.loc[
            (
                # ML 预测下跌
                (dataframe['&s-up_or_down'] == 0) &

                # 预测置信度高
                (dataframe['do_predict'] == 1) &

                (dataframe['volume'] > 0)
            ),
            'exit_long'] = 1

        return dataframe