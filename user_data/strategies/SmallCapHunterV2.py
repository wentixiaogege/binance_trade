"""
SmallCapHunterV2 — 顺大势 + 马丁网格
核心思路: 大周期定方向，小周期越跌越买/越涨越卖（DCA），等均值回归一把止盈

- 4h/1d 趋势判断方向（顺大势）
- 3m 价格偏离均线时入场
- 价格每继续偏离 3% → 加仓（马丁），最多加 3 次
- 整体盈利 3%+ → 全部平仓
- 资金费率极端时加大初始仓位

与 V1 的本质区别:
- V1: 追趋势，单点入场，止损
- V2: 顺趋势，网格 DCA，不加止损靠回归止盈
"""

import logging
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from pandas import DataFrame
import talib.abstract as ta
import numpy as np
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)


class SmallCapHunterV2(IStrategy):

    INTERFACE_VERSION: int = 3

    timeframe = '3m'
    startup_candle_count = 100

    minimal_roi = {"0": 1.0}
    trailing_stop = False
    use_custom_stoploss = False

    can_short = True
    process_only_new_candles = True

    # === 马丁网格核心 ===
    position_adjustment_enable = True
    max_entry_position_adjustment = 3  # 最多加仓 3 次（共 4 次入场）
    max_open_trades = 4  # 同时最多 4 个交易对

    protections = [
        {"method": "CooldownPeriod", "stop_duration_candles": 2},
        {"method": "StoplossGuard", "lookback_period_candles": 24,
         "trade_limit": 6, "stop_duration_candles": 8},
        {"method": "MaxDrawdown", "lookback_period_candles": 48,
         "trade_limit": 20, "stop_duration_candles": 24,
         "max_allowed_drawdown": 0.30},
    ]

    stoploss = -0.20  # 宽止损，主要靠趋势反转出场

    # === 杠杆 ===
    base_leverage_val = IntParameter(1, 3, default=2, space='buy')
    max_leverage_val = IntParameter(3, 6, default=4, space='buy')

    # === 网格间距 ===
    grid_step = DecimalParameter(0.02, 0.06, default=0.03, space='buy')

    # === 止盈目标 ===
    profit_target = DecimalParameter(0.02, 0.06, default=0.03, space='buy')

    # === 趋势判断 ===
    trend_lookback = IntParameter(50, 100, default=50, space='buy')

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        inf_pairs = [(pair, '4h') for pair in pairs]
        inf_pairs.append(('BTC/USDT:USDT', '4h'))
        return inf_pairs

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        if not isinstance(dataframe.index, pd.DatetimeIndex) and 'date' in dataframe.columns:
            dataframe.index = pd.to_datetime(dataframe['date'])

        # ATR
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['atr_ratio'] = dataframe['atr'] / dataframe['close']

        # 均线
        dataframe['sma_20'] = ta.SMA(dataframe, timeperiod=20)
        dataframe['sma_50'] = ta.SMA(dataframe, timeperiod=50)
        dataframe['ema_20'] = ta.EMA(dataframe, timeperiod=20)

        # 价格偏离均线程度
        dataframe['dev_sma20'] = (dataframe['close'] - dataframe['sma_20']) / dataframe['sma_20']

        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # 成交量
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=20)
        dataframe['volume_spike'] = dataframe['volume'] / dataframe['volume_ma']

        # 价格变化
        dataframe['change_3m'] = dataframe['close'].pct_change(3)

        # === 4h 趋势 + 翻转检测 ===
        inf_4h = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe='4h')
        if inf_4h is not None and len(inf_4h) >= self.trend_lookback.value + 1:
            df_4h = inf_4h.copy()
            if 'date' in df_4h.columns:
                df_4h['date'] = pd.to_datetime(df_4h['date'])
                df_4h.set_index('date', inplace=True)
            df_4h['sma_50'] = ta.SMA(df_4h, timeperiod=self.trend_lookback.value)
            df_4h['rsi'] = ta.RSI(df_4h, timeperiod=14)
            # 趋势定义
            df_4h['trend_bull'] = (
                (df_4h['close'] > df_4h['sma_50']) & (df_4h['rsi'] > 40)
            ).astype(int)
            df_4h['trend_bear'] = (
                (df_4h['close'] < df_4h['sma_50']) & (df_4h['rsi'] < 60)
            ).astype(int)
            # 趋势翻转点（大势翻了的瞬间）
            df_4h['flip_to_bull'] = (
                (df_4h['trend_bull'].shift(1) == 0) & (df_4h['trend_bull'] == 1)
            ).astype(int)
            df_4h['flip_to_bear'] = (
                (df_4h['trend_bear'].shift(1) == 0) & (df_4h['trend_bear'] == 1)
            ).astype(int)
            dataframe['trend_bull_4h'] = df_4h['trend_bull'].reindex(
                dataframe.index, method='ffill').fillna(0).astype(int)
            dataframe['trend_bear_4h'] = df_4h['trend_bear'].reindex(
                dataframe.index, method='ffill').fillna(0).astype(int)
            # 翻转信号（reindex 到 3m，翻转点后一段窗口内都可以入场）
            flip_bull_3m = df_4h['flip_to_bull'].reindex(
                dataframe.index, method='ffill').fillna(0)
            flip_bear_3m = df_4h['flip_to_bear'].reindex(
                dataframe.index, method='ffill').fillna(0)
            # 翻转后的窗口期（翻转后 48 根 3m K线 = 2.4h 内允许开网格）
            dataframe['flip_bull_window'] = (
                flip_bull_3m.rolling(48).max()
            ).fillna(0).astype(int)
            dataframe['flip_bear_window'] = (
                flip_bear_3m.rolling(48).max()
            ).fillna(0).astype(int)
        else:
            dataframe['trend_bull_4h'] = 0
            dataframe['trend_bear_4h'] = 0
            dataframe['flip_bull_window'] = 0
            dataframe['flip_bear_window'] = 0

        # === BTC 4h ===
        try:
            inf_btc = self.dp.get_pair_dataframe(pair='BTC/USDT:USDT', timeframe='4h')
            if inf_btc is not None and len(inf_btc) >= 5:
                df_btc = inf_btc.copy()
                if 'date' in df_btc.columns:
                    df_btc['date'] = pd.to_datetime(df_btc['date'])
                    df_btc.set_index('date', inplace=True)
                df_btc['change_12h'] = df_btc['close'].pct_change(3)
                dataframe['btc_change_12h'] = df_btc['change_12h'].reindex(
                    dataframe.index, method='ffill').fillna(0)
            else:
                dataframe['btc_change_12h'] = 0
        except Exception:
            dataframe['btc_change_12h'] = 0

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # === 做多网格: 4h多头趋势中，价格回落到SMA20下方就买，越跌越加 ===
        long_conditions = (
            (dataframe['trend_bull_4h'] == 1) &
            (dataframe['close'] < dataframe['sma_20']) &
            (dataframe['btc_change_12h'] > -0.10)
        )
        dataframe.loc[long_conditions, 'enter_long'] = 1
        dataframe.loc[long_conditions, 'enter_tag'] = 'grid_long'

        # === 做空网格: 4h空头趋势中，价格反弹到SMA20上方就卖，越涨越加 ===
        short_conditions = (
            (dataframe['trend_bear_4h'] == 1) &
            (dataframe['close'] > dataframe['sma_20']) &
            (dataframe['btc_change_12h'] < 0.10)
        )
        dataframe.loc[short_conditions, 'enter_short'] = 1
        dataframe.loc[short_conditions, 'enter_tag'] = 'grid_short'

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # 4h 趋势反转 → 全部平仓
        dataframe.loc[dataframe['trend_bull_4h'].shift(1) == 0, 'exit_short'] = (
            dataframe['trend_bull_4h'] == 1
        ).astype(int)
        dataframe.loc[dataframe['trend_bear_4h'].shift(1) == 0, 'exit_long'] = (
            dataframe['trend_bear_4h'] == 1
        ).astype(int)

        return dataframe

    # ========== 马丁加仓 ==========
    def adjust_entry_price(self, trade, order_type, amount, rate,
                           time_in_force, side, entry_tag, **kwargs):
        """
        每次加仓时，把订单价格调到更好的位置。
        做多: 挂单在当前价下方，等跌到位成交
        做空: 挂单在当前价上方，等涨到位成交
        """
        step = float(self.grid_step.value)

        # 第 N 次加仓（entry 0=初始, 1=第一次加仓, 2=第二次, 3=第三次）
        count = trade.nr_of_successful_entries

        if trade.is_short:
            # 做空：越涨越卖，每次抬高 step
            adjusted = rate * (1 + step * (count + 1))
        else:
            # 做多：越跌越买，每次降低 step
            adjusted = rate * (1 - step * (count + 1))

        return adjusted

    # ========== 马丁仓位（越加越大）==========
    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            entry_tag: str, side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake

        last = dataframe.iloc[-1].squeeze()
        atr_ratio = last.get('atr_ratio', 0.03)

        # 波动率调整基础仓位
        if atr_ratio > 0.06:
            stake = proposed_stake * 0.3
        elif atr_ratio > 0.04:
            stake = proposed_stake * 0.5
        elif atr_ratio > 0.025:
            stake = proposed_stake * 0.7
        else:
            stake = proposed_stake

        # 资金费率共振
        try:
            fr = self.dp.funding_rate(pair)
            funding_rate = fr.get('fundingRate', 0) if fr else 0
        except Exception:
            funding_rate = 0

        if side == 'long' and funding_rate < -0.001:
            stake = stake * 1.5
        elif side == 'long' and funding_rate < -0.0005:
            stake = stake * 1.2
        if side == 'short' and funding_rate > 0.001:
            stake = stake * 1.5
        elif side == 'short' and funding_rate > 0.0005:
            stake = stake * 1.2

        return max(min_stake, min(stake, max_stake))

    # ========== 杠杆 ==========
    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return float(self.base_leverage_val.value)
        last = dataframe.iloc[-1].squeeze()
        atr_ratio = last.get('atr_ratio', 0.03)
        base = float(self.base_leverage_val.value)
        max_lev = float(self.max_leverage_val.value)
        if atr_ratio > 0.06:
            return base
        return min(base + 1, max_lev)

    # ========== 动态出场 ==========
    def custom_exit(self, pair: str, trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):

        leverage = trade.leverage if hasattr(trade, 'leverage') else 3.0
        if current_profit < -(1.0 / leverage) * 0.85:
            return 'liquidation_risk'

        # 整体盈利达到目标 → 全部平仓
        target = float(self.profit_target.value)
        if current_profit > target:
            return 'grid_tp'

        return None
