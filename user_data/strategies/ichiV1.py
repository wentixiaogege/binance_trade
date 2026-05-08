# --- Do not remove these libs ---
from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
import pandas as pd  # noqa
pd.options.mode.chained_assignment = None  # default='warn'
import technical.indicators as ftt
from functools import reduce
from datetime import datetime, timedelta
from freqtrade.strategy import merge_informative_pair
import numpy as np
from freqtrade.strategy import stoploss_from_open

#https://strategy.insightpearl.com/results/1583
class ichiV1(IStrategy):
    """
    ichiV1 策略 - V3版本
    基于Ichimoku云图和多时间框架趋势分析的交易策略

    策略核心思想：
    1. 使用Ichimoku云图判断市场趋势方向
    2. 通过多时间框架EMA分析确认趋势强度
    3. 利用扇形幅度(fan magnitude)捕捉趋势加速信号
    4. 结合Heikin Ashi蜡烛图平滑价格波动
    """

    # NOTE: settings as of the 25th july 21
    # Buy hyperspace params:
    buy_params = {
        # 趋势高于Senkou云图的级别要求 (1-8级别，数字越大要求越严格), 默认为1
        # 级别1: 仅要求5分钟趋势高于云图
        # 级别8: 要求所有时间框架(5m-8h)趋势都高于云图
        "buy_trend_above_senkou_level": 1,

        # 趋势看涨的级别要求 (1-8级别，数字越大要求越严格), 默认为6
        # 级别1: 仅要求5分钟趋势看涨(收盘价>开盘价)
        # 级别6: 要求6个时间框架趋势都看涨
        # 级别8: 要求所有时间框架趋势都看涨
        "buy_trend_bullish_level": 4,

        # 扇形幅度连续上升的周期数 (默认3个周期)
        # 用于确认趋势加速，要求当前扇形幅度大于前N个周期
        "buy_fan_magnitude_shift_value": 3,

        # 扇形幅度增益的最小要求 (默认1.002，即0.2%的增长)
        # 1.002: 较宽松条件，胜率约70%，交易次数较多
        # 1.008: 较严格条件，胜率约90%，仅捕捉最大的趋势移动
        "buy_min_fan_magnitude_gain": 1.001  # NOTE: Good value (Win% ~70%), alot of trades
        #"buy_min_fan_magnitude_gain": 1.008 # NOTE: Very save value (Win% ~90%), only the biggest moves 1.008,
    }

    # Sell hyperspace params:
    # NOTE: was 15m but kept bailing out in dryrun
    sell_params = {
        # 卖出信号使用的趋势指标 (可选: trend_close_5m, trend_close_15m, trend_close_30m,
        # trend_close_1h, trend_close_2h, trend_close_4h, trend_close_6h, trend_close_8h)
        # 当5分钟趋势跌破选定的趋势线时触发卖出信号
        # 使用2小时趋势线可以避免在模拟交易中过早退出
        "sell_trend_indicator": "trend_close_1h",
    }

    # ROI table: 分阶段止盈设置
    # "时间(分钟)": 止盈百分比
    minimal_roi = {
        "0": 0.059,    # 开仓后立即可获利5.9%
        "10": 0.037,   # 10分钟后降至3.7%
        "41": 0.012,   # 41分钟后降至1.2%
        "114": 0       # 114分钟后无止盈要求
    }

    # Stoploss: 止损设置 (-27.5%)
    stoploss = -0.275

    # Optimal timeframe for the strategy: 策略运行的最佳时间框架
    timeframe = '5m'

    # 启动时需要的历史蜡烛数量 (96根5分钟蜡烛 = 8小时)
    startup_candle_count = 96

    # 是否仅处理新蜡烛 (False表示每次tick都重新计算)
    process_only_new_candles = False

    # 追踪止损设置 (当前禁用)
    trailing_stop = False
    #trailing_stop_positive = 0.002        # 正向追踪止损阈值
    #trailing_stop_positive_offset = 0.025  # 追踪止损偏移量
    #trailing_only_offset_is_reached = True # 仅在达到偏移量后启用追踪

    # 信号使用设置
    use_sell_signal = True          # 使用卖出信号
    sell_profit_only = False        # 不仅在盈利时卖出
    ignore_roi_if_buy_signal = False # 有买入信号时不忽略ROI

    # 图表配置 - 用于可视化策略指标
    plot_config = {
        'main_plot': {
            # 在senkou_a和senkou_b之间填充区域 (Ichimoku云图)
            'senkou_a': {
                'color': 'green', #optional
                'fill_to': 'senkou_b',
                'fill_label': 'Ichimoku Cloud', #optional
                'fill_color': 'rgba(255,76,46,0.2)', #optional
            },
            # 同时绘制senkou_b线
            'senkou_b': {},
            # 不同时间框架的趋势线，使用不同颜色区分
            'trend_close_5m': {'color': '#FF5733'},   # 红色
            'trend_close_15m': {'color': '#FF8333'},  # 橙红色
            'trend_close_30m': {'color': '#FFB533'},  # 橙色
            'trend_close_1h': {'color': '#FFE633'},   # 黄色
            'trend_close_2h': {'color': '#E3FF33'},   # 黄绿色
            'trend_close_4h': {'color': '#C4FF33'},   # 绿黄色
            'trend_close_6h': {'color': '#61FF33'},   # 绿色
            'trend_close_8h': {'color': '#33FF7D'}    # 青绿色
        },
        'subplots': {
            # 扇形幅度子图
            'fan_magnitude': {
                'fan_magnitude': {}
            },
            # 扇形幅度增益子图
            'fan_magnitude_gain': {
                'fan_magnitude_gain': {}
            }
        }
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        填充技术指标

        主要指标包括：
        1. Heikin Ashi蜡烛图 - 平滑价格波动
        2. 多时间框架EMA趋势线 - 分析不同周期趋势
        3. 扇形幅度指标 - 衡量趋势强度和加速度
        4. Ichimoku云图指标 - 判断趋势方向和支撑阻力
        5. ATR指标 - 衡量市场波动性
        """

        # 计算Heikin Ashi蜡烛图，用于平滑价格波动
        heikinashi = qtpylib.heikinashi(dataframe)
        dataframe['open'] = heikinashi['open']
        #dataframe['close'] = heikinashi['close']  # 保持原始收盘价
        dataframe['high'] = heikinashi['high']
        dataframe['low'] = heikinashi['low']

        # 计算多时间框架的收盘价趋势线 (使用EMA平滑)
        dataframe['trend_close_5m'] = dataframe['close']                    # 5分钟: 原始收盘价
        dataframe['trend_close_15m'] = ta.EMA(dataframe['close'], timeperiod=3)   # 15分钟: 3周期EMA
        dataframe['trend_close_30m'] = ta.EMA(dataframe['close'], timeperiod=6)   # 30分钟: 6周期EMA
        dataframe['trend_close_1h'] = ta.EMA(dataframe['close'], timeperiod=12)   # 1小时: 12周期EMA
        dataframe['trend_close_2h'] = ta.EMA(dataframe['close'], timeperiod=24)   # 2小时: 24周期EMA
        dataframe['trend_close_4h'] = ta.EMA(dataframe['close'], timeperiod=48)   # 4小时: 48周期EMA
        dataframe['trend_close_6h'] = ta.EMA(dataframe['close'], timeperiod=72)   # 6小时: 72周期EMA
        dataframe['trend_close_8h'] = ta.EMA(dataframe['close'], timeperiod=96)   # 8小时: 96周期EMA

        # 计算多时间框架的开盘价趋势线 (使用EMA平滑)
        dataframe['trend_open_5m'] = dataframe['open']                     # 5分钟: 原始开盘价
        dataframe['trend_open_15m'] = ta.EMA(dataframe['open'], timeperiod=3)    # 15分钟: 3周期EMA
        dataframe['trend_open_30m'] = ta.EMA(dataframe['open'], timeperiod=6)    # 30分钟: 6周期EMA
        dataframe['trend_open_1h'] = ta.EMA(dataframe['open'], timeperiod=12)    # 1小时: 12周期EMA
        dataframe['trend_open_2h'] = ta.EMA(dataframe['open'], timeperiod=24)    # 2小时: 24周期EMA
        dataframe['trend_open_4h'] = ta.EMA(dataframe['open'], timeperiod=48)    # 4小时: 48周期EMA
        dataframe['trend_open_6h'] = ta.EMA(dataframe['open'], timeperiod=72)    # 6小时: 72周期EMA
        dataframe['trend_open_8h'] = ta.EMA(dataframe['open'], timeperiod=96)    # 8小时: 96周期EMA

        # 计算扇形幅度 - 衡量短期趋势相对于长期趋势的强度
        # 比值>1表示短期趋势强于长期趋势(看涨)，<1表示相反(看跌)
        dataframe['fan_magnitude'] = (dataframe['trend_close_1h'] / dataframe['trend_close_8h'])

        # 计算扇形幅度增益 - 衡量趋势加速度
        # >1表示趋势在加速，<1表示趋势在减速
        dataframe['fan_magnitude_gain'] = dataframe['fan_magnitude'] / dataframe['fan_magnitude'].shift(1)

        # 计算Ichimoku云图指标
        # 使用自定义参数: 转换线20, 基准线60, 滞后跨度120, 位移30
        ichimoku = ftt.ichimoku(dataframe, conversion_line_period=20, base_line_periods=60, laggin_span=120, displacement=30)
        dataframe['chikou_span'] = ichimoku['chikou_span']                    # 滞后跨度
        dataframe['tenkan_sen'] = ichimoku['tenkan_sen']                      # 转换线
        dataframe['kijun_sen'] = ichimoku['kijun_sen']                        # 基准线
        dataframe['senkou_a'] = ichimoku['senkou_span_a']                     # 先行跨度A
        dataframe['senkou_b'] = ichimoku['senkou_span_b']                     # 先行跨度B
        dataframe['leading_senkou_span_a'] = ichimoku['leading_senkou_span_a'] # 领先先行跨度A
        dataframe['leading_senkou_span_b'] = ichimoku['leading_senkou_span_b'] # 领先先行跨度B
        dataframe['cloud_green'] = ichimoku['cloud_green']                   # 绿云(看涨云)
        dataframe['cloud_red'] = ichimoku['cloud_red']                       # 红云(看跌云)

        # 计算ATR (Average True Range) - 衡量市场波动性
        dataframe['atr'] = ta.ATR(dataframe)

        return dataframe


    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        填充入场(买入)信号

        入场条件组合：
        1. 趋势高于Ichimoku云图条件 - 确保处于上升趋势
        2. 多时间框架看涨条件 - 确保趋势一致性
        3. 扇形幅度条件 - 确保趋势强度和加速度

        所有条件必须同时满足才会产生买入信号
        """

        conditions = []

        # 条件组1: 趋势高于Senkou云图 - 确保价格在云图之上(看涨区域)
        # 根据buy_trend_above_senkou_level参数决定检查多少个时间框架
        if self.buy_params['buy_trend_above_senkou_level'] >= 1:
            # 5分钟趋势必须高于云图上下边界
            conditions.append(dataframe['trend_close_5m'] > dataframe['senkou_a'])
            conditions.append(dataframe['trend_close_5m'] > dataframe['senkou_b'])

        if self.buy_params['buy_trend_above_senkou_level'] >= 2:
            # 15分钟趋势也必须高于云图
            conditions.append(dataframe['trend_close_15m'] > dataframe['senkou_a'])
            conditions.append(dataframe['trend_close_15m'] > dataframe['senkou_b'])

        if self.buy_params['buy_trend_above_senkou_level'] >= 3:
            # 30分钟趋势也必须高于云图
            conditions.append(dataframe['trend_close_30m'] > dataframe['senkou_a'])
            conditions.append(dataframe['trend_close_30m'] > dataframe['senkou_b'])

        if self.buy_params['buy_trend_above_senkou_level'] >= 4:
            # 1小时趋势也必须高于云图
            conditions.append(dataframe['trend_close_1h'] > dataframe['senkou_a'])
            conditions.append(dataframe['trend_close_1h'] > dataframe['senkou_b'])

        if self.buy_params['buy_trend_above_senkou_level'] >= 5:
            # 2小时趋势也必须高于云图
            conditions.append(dataframe['trend_close_2h'] > dataframe['senkou_a'])
            conditions.append(dataframe['trend_close_2h'] > dataframe['senkou_b'])

        if self.buy_params['buy_trend_above_senkou_level'] >= 6:
            # 4小时趋势也必须高于云图
            conditions.append(dataframe['trend_close_4h'] > dataframe['senkou_a'])
            conditions.append(dataframe['trend_close_4h'] > dataframe['senkou_b'])

        if self.buy_params['buy_trend_above_senkou_level'] >= 7:
            # 6小时趋势也必须高于云图
            conditions.append(dataframe['trend_close_6h'] > dataframe['senkou_a'])
            conditions.append(dataframe['trend_close_6h'] > dataframe['senkou_b'])

        if self.buy_params['buy_trend_above_senkou_level'] >= 8:
            # 8小时趋势也必须高于云图
            conditions.append(dataframe['trend_close_8h'] > dataframe['senkou_a'])
            conditions.append(dataframe['trend_close_8h'] > dataframe['senkou_b'])

        # 条件组2: 趋势看涨 - 确保各时间框架收盘价高于开盘价
        # 根据buy_trend_bullish_level参数决定检查多少个时间框架
        if self.buy_params['buy_trend_bullish_level'] >= 1:
            # 5分钟趋势看涨
            conditions.append(dataframe['trend_close_5m'] > dataframe['trend_open_5m'])

        if self.buy_params['buy_trend_bullish_level'] >= 2:
            # 15分钟趋势看涨
            conditions.append(dataframe['trend_close_15m'] > dataframe['trend_open_15m'])

        if self.buy_params['buy_trend_bullish_level'] >= 3:
            # 30分钟趋势看涨
            conditions.append(dataframe['trend_close_30m'] > dataframe['trend_open_30m'])

        if self.buy_params['buy_trend_bullish_level'] >= 4:
            # 1小时趋势看涨
            conditions.append(dataframe['trend_close_1h'] > dataframe['trend_open_1h'])

        if self.buy_params['buy_trend_bullish_level'] >= 5:
            # 2小时趋势看涨
            conditions.append(dataframe['trend_close_2h'] > dataframe['trend_open_2h'])

        if self.buy_params['buy_trend_bullish_level'] >= 6:
            # 4小时趋势看涨
            conditions.append(dataframe['trend_close_4h'] > dataframe['trend_open_4h'])

        if self.buy_params['buy_trend_bullish_level'] >= 7:
            # 6小时趋势看涨
            conditions.append(dataframe['trend_close_6h'] > dataframe['trend_open_6h'])

        if self.buy_params['buy_trend_bullish_level'] >= 8:
            # 8小时趋势看涨
            conditions.append(dataframe['trend_close_8h'] > dataframe['trend_open_8h'])

        # 条件组3: 扇形幅度条件 - 确保趋势强度和加速度
        # 扇形幅度增益必须达到最小要求(趋势加速)
        conditions.append(dataframe['fan_magnitude_gain'] >= self.buy_params['buy_min_fan_magnitude_gain'])

        # 扇形幅度必须大于1(短期趋势强于长期趋势)
        conditions.append(dataframe['fan_magnitude'] > 1)

        # 扇形幅度必须连续上升(趋势持续加强)
        # 检查前N个周期，确保当前值大于历史值
        for x in range(self.buy_params['buy_fan_magnitude_shift_value']):
            conditions.append(dataframe['fan_magnitude'].shift(x+1) < dataframe['fan_magnitude'])

        # 当所有条件都满足时，设置买入信号
        if conditions:
            dataframe.loc[
                reduce(lambda x, y: x & y, conditions),
                'enter_long'] = 1  # V3版本使用enter_long替代buy

        return dataframe


    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        填充退场(卖出)信号

        退场条件：
        当5分钟趋势线跌破指定的长期趋势线时触发卖出信号
        使用crossed_below函数检测趋势线的向下突破

        默认使用2小时趋势线作为退场参考，可通过sell_trend_indicator参数调整
        """

        conditions = []

        # 主要退场条件: 5分钟趋势跌破选定的长期趋势线
        # 这表明短期趋势开始转弱，应该退出多头仓位
        conditions.append(qtpylib.crossed_below(
            dataframe['trend_close_5m'],
            dataframe[self.sell_params['sell_trend_indicator']]
        ))

        # 当退场条件满足时，设置卖出信号
        if conditions:
            dataframe.loc[
                reduce(lambda x, y: x & y, conditions),
                'exit_long'] = 1  # V3版本使用exit_long替代sell

        return dataframe