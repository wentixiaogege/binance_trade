"""
SmallCapHunterV2 — 布林带 + 马丁网格 v3
核心: 1h趋势定方向 + 3m BB极端回调入场 + 马丁加仓 + 止盈/止损出口
关键改动: 去掉exit_signal, 只靠止盈和止损出场, 避免手续费环境下频繁小亏
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

    position_adjustment_enable = True
    max_entry_position_adjustment = 3
    max_open_trades = 5

    protections = [
        {"method": "CooldownPeriod", "stop_duration_candles": 6},
        {"method": "StoplossGuard", "lookback_period_candles": 24,
         "trade_limit": 4, "stop_duration_candles": 12},
        {"method": "MaxDrawdown", "lookback_period_candles": 48,
         "trade_limit": 20, "stop_duration_candles": 24,
         "max_allowed_drawdown": 0.25},
    ]

    stoploss = -0.35

    # === 杠杆 ===
    base_leverage_val = IntParameter(1, 2, default=1, space='buy')
    max_leverage_val = IntParameter(2, 3, default=2, space='buy')

    # === 马丁网格参数 (更大的步长和止盈来覆盖手续费) ===
    grid_step = DecimalParameter(0.03, 0.06, default=0.04, space='buy')
    profit_target = DecimalParameter(0.025, 0.05, default=0.035, space='buy')

    # === RSI (极端的阈值确保高质量入场) ===
    rsi_entry_long = IntParameter(20, 30, default=25, space='buy')
    rsi_entry_short = IntParameter(70, 80, default=75, space='buy')

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(pair, '1h') for pair in pairs]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        if not isinstance(dataframe.index, pd.DatetimeIndex) and 'date' in dataframe.columns:
            dataframe.index = pd.to_datetime(dataframe['date'])

        # === 3m 指标 ===
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['atr_ratio'] = dataframe['atr'] / dataframe['close']

        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        bb = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.5, nbdevdn=2.5)
        dataframe['bb_lower'] = bb['lowerband']
        dataframe['bb_mid'] = bb['middleband']
        dataframe['bb_upper'] = bb['upperband']
        dataframe['bb_position'] = (dataframe['close'] - dataframe['bb_lower']) / (
            dataframe['bb_upper'] - dataframe['bb_lower'] + 0.0001
        )

        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=20)
        dataframe['volume_ratio'] = dataframe['volume'] / dataframe['volume_ma']

        # === 1h 趋势 ===
        inf_1h = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe='1h')
        if inf_1h is not None and len(inf_1h) >= 30:
            df_1h = inf_1h.copy()
            if 'date' in df_1h.columns:
                df_1h['date'] = pd.to_datetime(df_1h['date'])
                df_1h.set_index('date', inplace=True)

            df_1h['ema_20'] = ta.EMA(df_1h, timeperiod=20)
            df_1h['ema_50'] = ta.EMA(df_1h, timeperiod=50)
            df_1h['adx'] = ta.ADX(df_1h, timeperiod=14)

            # 强趋势：EMA排列 + close>EMA20 + ADX确认
            df_1h['strong_bull'] = (
                (df_1h['ema_20'] > df_1h['ema_50']) &
                (df_1h['close'] > df_1h['ema_20']) &
                (df_1h['adx'] > 22)
            ).astype(int)
            df_1h['strong_bear'] = (
                (df_1h['ema_20'] < df_1h['ema_50']) &
                (df_1h['close'] < df_1h['ema_20']) &
                (df_1h['adx'] > 22)
            ).astype(int)

            # 趋势反转信号
            df_1h['bull_to_bear'] = (
                (df_1h['strong_bull'].shift(1) == 1) & (df_1h['strong_bear'] == 1)
            ).astype(int)
            df_1h['bear_to_bull'] = (
                (df_1h['strong_bear'].shift(1) == 1) & (df_1h['strong_bull'] == 1)
            ).astype(int)

            dataframe['strong_bull_1h'] = df_1h['strong_bull'].reindex(
                dataframe.index, method='ffill').fillna(0).astype(int)
            dataframe['strong_bear_1h'] = df_1h['strong_bear'].reindex(
                dataframe.index, method='ffill').fillna(0).astype(int)

            # 趋势反转（reindex到3m）
            btob_3m = df_1h['bull_to_bear'].reindex(
                dataframe.index, method='ffill').fillna(0)
            btol_3m = df_1h['bear_to_bull'].reindex(
                dataframe.index, method='ffill').fillna(0)
            dataframe['trend_reverse_long'] = (
                btob_3m.rolling(24, min_periods=1).max()
            ).fillna(0).astype(int)
            dataframe['trend_reverse_short'] = (
                btol_3m.rolling(24, min_periods=1).max()
            ).fillna(0).astype(int)
        else:
            dataframe['strong_bull_1h'] = 0
            dataframe['strong_bear_1h'] = 0
            dataframe['trend_reverse_long'] = 0
            dataframe['trend_reverse_short'] = 0

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        rsi_long = self.rsi_entry_long.value
        rsi_short = self.rsi_entry_short.value

        # === 做多网格：1h强多头 + 3m BB极端超卖 ===
        long_conditions = (
            (dataframe['strong_bull_1h'] == 1) &
            (dataframe['bb_position'] < 0.10) &
            (dataframe['rsi'] < rsi_long) &
            (dataframe['volume_ratio'] > 0.5) &
            (dataframe['atr_ratio'] < 0.06)
        )
        dataframe.loc[long_conditions, 'enter_long'] = 1
        dataframe.loc[long_conditions, 'enter_tag'] = 'martin_long'

        # === 做空网格：1h强空头 + 3m BB极端超买 ===
        short_conditions = (
            (dataframe['strong_bear_1h'] == 1) &
            (dataframe['bb_position'] > 0.90) &
            (dataframe['rsi'] > rsi_short) &
            (dataframe['volume_ratio'] > 0.5) &
            (dataframe['atr_ratio'] < 0.06)
        )
        dataframe.loc[short_conditions, 'enter_short'] = 1
        dataframe.loc[short_conditions, 'enter_tag'] = 'martin_short'

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # 只在趋势真正反转时退出，不在趋势消失时退出
        dataframe.loc[dataframe['trend_reverse_long'] == 1, 'exit_long'] = 1
        dataframe.loc[dataframe['trend_reverse_short'] == 1, 'exit_short'] = 1

        return dataframe

    # ========== 马丁加仓 ==========
    def adjust_entry_price(self, trade, order_type, amount, rate,
                           time_in_force, side, entry_tag, **kwargs):
        step = float(self.grid_step.value)
        count = trade.nr_of_successful_entries

        if trade.is_short:
            adjusted = rate * (1 + step * (count + 1))
        else:
            adjusted = rate * (1 - step * (count + 1))

        return adjusted

    # ========== 仓位 ==========
    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            entry_tag: str, side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake

        last = dataframe.iloc[-1].squeeze()
        atr_ratio = last.get('atr_ratio', 0.03)

        if atr_ratio > 0.06:
            stake = proposed_stake * 0.3
        elif atr_ratio > 0.04:
            stake = proposed_stake * 0.5
        elif atr_ratio > 0.025:
            stake = proposed_stake * 0.7
        else:
            stake = proposed_stake

        return max(min_stake, min(stake, max_stake))

    # ========== 止盈/止损出口 ==========
    def custom_exit(self, pair: str, trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):

        leverage = trade.leverage if hasattr(trade, 'leverage') else 2.0

        if current_profit < -(1.0 / leverage) * 0.70:
            return 'liquidation_warn'

        target = float(self.profit_target.value)
        if current_profit > target:
            return 'martin_tp'

        # BB中轨回归 + 有盈利
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) > 0:
            last = dataframe.iloc[-1].squeeze()
            bb_pos = last.get('bb_position', 0.5)
            rsi = last.get('rsi', 50)

            if not trade.is_short and current_profit > 0.015 and bb_pos > 0.45:
                return 'bb_mid_long'
            if trade.is_short and current_profit > 0.015 and bb_pos < 0.55:
                return 'bb_mid_short'
            if not trade.is_short and current_profit > 0.01 and rsi > 50:
                return 'rsi_rec_long'
            if trade.is_short and current_profit > 0.01 and rsi < 50:
                return 'rsi_rec_short'

        return None

    # ========== 杠杆 ==========
    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return float(self.base_leverage_val.value)
        last = dataframe.iloc[-1].squeeze()
        atr_ratio = last.get('atr_ratio', 0.03)
        return float(self.base_leverage_val.value) if atr_ratio > 0.05 else float(self.max_leverage_val.value)
