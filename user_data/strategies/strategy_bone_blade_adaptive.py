# strategy_bone_blade_adaptive.py
import pandas as pd
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
from pandas import DataFrame
from functools import reduce
from datetime import datetime
import numpy as np
from freqtrade.strategy import IStrategy, DecimalParameter, IntParameter
from freqtrade.persistence import Trade

class BoneBladeAdaptiveStrategy(IStrategy):
    """
    骨刃策略（自适应版）
    核心：高频波段交易，集成市场状态检测，根据牛/熊/震荡/周末/异常日动态调整参数。
    """

    # 参数定义（可调优）
    buy_params = {
        'bb_period': 20,
        'bb_std': 2.0,
        'rsi_period': 14,
        'rsi_oversold': 30,
        'volume_spike_factor': 1.8,
        'ema_short': 50,
        'ema_long': 200,
        'adx_threshold': 25,
        'atr_multiplier': 2.0,
        'bull_risk_factor': 1.2,      # 牛市放宽条件
        'bear_risk_factor': 0.8,       # 熊市收紧条件
        'weekend_disable': False,      # 周末是否禁用交易
        'month_anomaly_disable': True, # 异常月份是否禁用
    }

    # ROI 表
    minimal_roi = {
        "0": 0.05,
        "5": 0.03,
        "15": 0.02,
        "30": 0.01,
        "60": 0
    }

    stoploss = -0.10
    timeframe = '5m'
    startup_candle_count = 200
    process_only_new_candles = False
    use_sell_signal = True
    sell_profit_only = False
    ignore_roi_if_buy_signal = False

    # 异常日期配置（可根据需要修改）
    ANOMALY_DATES = [
        ('01-20', '02-10'),  # 中国春节前后
        ('11-22', '11-28'),  # 美国感恩节
        ('12-20', '01-05'),  # 圣诞-新年
    ]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 基础指标
        bollinger = ta.BBANDS(dataframe, timeperiod=self.buy_params['bb_period'],
                               nbdevup=self.buy_params['bb_std'], nbdevdn=self.buy_params['bb_std'])
        dataframe['bb_lower'] = bollinger['lowerband']
        dataframe['bb_middle'] = bollinger['middleband']
        dataframe['bb_upper'] = bollinger['upperband']
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=self.buy_params['rsi_period'])
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=20)
        dataframe['volume_spike'] = dataframe['volume'] > (dataframe['volume_ma'] * self.buy_params['volume_spike_factor'])
        dataframe['ema_short'] = ta.EMA(dataframe, timeperiod=self.buy_params['ema_short'])
        dataframe['ema_long'] = ta.EMA(dataframe, timeperiod=self.buy_params['ema_long'])
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)

        # 市场状态检测
        self._detect_market_phase(dataframe)
        return dataframe

    def _detect_market_phase(self, dataframe: DataFrame):
        """检测市场状态并添加列"""
        # 趋势方向：价格与EMA200的相对位置
        ema200 = ta.EMA(dataframe['close'], timeperiod=200)
        price_vs_ema200 = (dataframe['close'] - ema200) / ema200
        dataframe['trend_bull'] = (price_vs_ema200 > 0.03) & (dataframe['adx'] > 25)
        dataframe['trend_bear'] = (price_vs_ema200 < -0.03) & (dataframe['adx'] > 25)
        dataframe['trend_ranging'] = ~(dataframe['trend_bull'] | dataframe['trend_bear'])

        # 波动率状态（基于ATR百分比）
        volatility = dataframe['atr'] / dataframe['close']
        vol_ma = volatility.rolling(50).mean()
        dataframe['vol_high'] = volatility > vol_ma * 1.5
        dataframe['vol_low'] = volatility < vol_ma * 0.5

        # 周末标志（使用日期索引，需在外部传入当前时间，但这里无法实时，通过custom_exit实时判断）
        # 在custom_exit中处理

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 基础买入条件
        base_conditions = [
            (dataframe['close'] <= dataframe['bb_lower']),
            (dataframe['rsi'] < self.buy_params['rsi_oversold']),
            (dataframe['volume_spike'] == True),
            (dataframe['ema_short'] > dataframe['ema_long']),
            (dataframe['adx'] > self.buy_params['adx_threshold']),
        ]

        # 根据市场状态调整条件强度
        # 牛市放宽要求，熊市收紧
        factor = self.buy_params['bull_risk_factor']  # 默认为1.2，可理解为额外放宽
        # 在熊市中，要求更严格的RSI
        rsi_adjusted = self.buy_params['rsi_oversold'] * (1 if dataframe['trend_bull'].iloc[-1] else 0.9)

        # 构建最终条件
        final_conditions = base_conditions.copy()
        final_conditions.append(dataframe['rsi'] < rsi_adjusted)

        # 在震荡市增加布林带宽度条件
        if dataframe['trend_ranging'].iloc[-1]:
            bb_width = (dataframe['bb_upper'] - dataframe['bb_lower']) / dataframe['bb_middle']
            final_conditions.append(bb_width > bb_width.rolling(20).mean())

        # 周末禁用（通过custom_exit处理，但也可在入场过滤）
        # 在custom_entry中动态判断

        if final_conditions:
            dataframe.loc[reduce(lambda x, y: x & y, final_conditions), 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            (dataframe['close'] >= dataframe['bb_upper']) & (dataframe['rsi'] > 70),
            qtpylib.crossed_below(dataframe['close'], dataframe['ema_short']),
        ]
        dataframe.loc[reduce(lambda x, y: x | y, conditions), 'exit_long'] = 1
        return dataframe

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """动态止损：根据市场波动和状态调整"""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return self.stoploss

        last_candle = dataframe.iloc[-1].squeeze()
        atr = last_candle.get('atr', 0)
        if atr <= 0:
            return self.stoploss

        # 基础止损距离 = ATR * multiplier
        base_multiplier = self.buy_params['atr_multiplier']
        # 在熊市中收紧止损
        if last_candle.get('trend_bear', False):
            base_multiplier *= 0.8
        # 在周末放宽止损（避免被插针扫损）
        if self._is_weekend(current_time):
            base_multiplier *= 1.2

        stoploss_price = current_rate - atr * base_multiplier
        stoploss_ratio = (stoploss_price - trade.open_rate) / trade.open_rate
        return max(stoploss_ratio, self.stoploss)

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            entry_tag: str, **kwargs) -> float:
        """动态仓位：根据市场状态调整仓位大小"""
        # 获取市场状态
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake

        last_candle = dataframe.iloc[-1].squeeze()
        base_stake = proposed_stake
        risk_factor = 1.0

        # 熊市减仓
        if last_candle.get('trend_bear', False):
            risk_factor *= 0.5
        # 高波动减仓
        if last_candle.get('vol_high', False):
            risk_factor *= 0.7
        # 周末减仓
        if self._is_weekend(current_time):
            risk_factor *= 0.5
        # 异常月份减仓
        if self._is_anomaly_month(current_time):
            risk_factor *= 0.3

        adjusted_stake = base_stake * risk_factor
        return max(min_stake, min(adjusted_stake, max_stake))

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        """额外退出条件：周末平仓、异常日平仓、时间止损等"""
        # 周末强制平仓
        if self._is_weekend(current_time) and self.buy_params['weekend_disable']:
            return 'weekend_exit'
        # 异常日期强制平仓
        if self._is_anomaly_date(current_time) and self.buy_params['month_anomaly_disable']:
            return 'anomaly_exit'
        return None

    # 辅助函数
    def _is_weekend(self, current_time: datetime) -> bool:
        return current_time.weekday() >= 5

    def _is_anomaly_date(self, current_time: datetime) -> bool:
        """判断是否在异常日期范围内"""
        date_str = current_time.strftime('%m-%d')
        for start, end in self.ANOMALY_DATES:
            if start <= date_str <= end:
                return True
        return False

    def _is_anomaly_month(self, current_time: datetime) -> bool:
        """判断是否在异常月份（如1、12月）"""
        month = current_time.month
        return month in [1, 12]  # 可根据需要调整