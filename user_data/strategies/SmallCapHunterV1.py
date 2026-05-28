"""
SmallCapHunterV1 — 小币种趋势猎手
核心思路: 大周期定方向，小周期找 exhaustion 点入场

多周期分工:
- 3m: 核心主周期 — OTT 翻转入场/出场信号
- 15m: 近期趋势确认（入场需要15m同向）
- 4h/1d: 中长期趋势判断 + 大级别翻转出场

趋势检测:
- 1d/4h/15m: pytrendseries 峰值-谷底检测，直接识别价格形态
- 3m: OTT 指标用于精确入场/出场时点

信号链:
- 做多: 1d/4h/15m 看涨 + 3m OTT 刚翻多（回调结束入场）
- 做空: 1d/4h/15m 看空 + 3m OTT 刚翻空 + BTC弱势
- 出场: 3m OTT 反向翻转（小级别反转）或 4h OTT 翻向（大趋势反转）
"""

import logging
import os
import sys
from numpy.lib import math
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from pandas import DataFrame, Series
import talib.abstract as ta
import numpy as np
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)


class SmallCapHunterV1(IStrategy):

    INTERFACE_VERSION: int = 3

    timeframe = '3m'
    startup_candle_count = 200

    # 风控
    minimal_roi = {"0": 1.0}
    stoploss = -0.25
    trailing_stop = True
    trailing_stop_positive = 0.15
    trailing_stop_positive_offset = 0.30
    trailing_only_offset_is_reached = False
    use_custom_stoploss = False

    can_short = True
    process_only_new_candles = True
    max_open_trades = 3

    protections = [
        {"method": "CooldownPeriod", "stop_duration_candles": 4},
        {"method": "StoplossGuard", "lookback_period_candles": 24,
         "trade_limit": 4, "stop_duration_candles": 12},
        {"method": "MaxDrawdown", "lookback_period_candles": 48,
         "trade_limit": 20, "stop_duration_candles": 24,
         "max_allowed_drawdown": 0.20},
    ]

    # 超参
    ott_percent = DecimalParameter(1.0, 2.5, default=1.6, space='buy')
    ott_pds = IntParameter(2, 4, default=2, space='buy')
    ott_cmo_period = IntParameter(7, 12, default=9, space='buy')
    base_leverage_val = IntParameter(2, 5, default=3, space='buy')
    max_leverage_val = IntParameter(3, 8, default=5, space='buy')

    # pytrendseries 参数 (window 根据各周期数据量调整)
    trend_1d_window = IntParameter(20, 40, default=30, space='buy')
    trend_4h_window = IntParameter(40, 80, default=60, space='buy')
    trend_15m_window = IntParameter(128, 256, default=192, space='buy')

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        inf_pairs = []
        for pair in pairs:
            inf_pairs.extend([
                (pair, '15m'), (pair, '4h'), (pair, '1d'),
            ])
        inf_pairs.append(('BTC/USDT:USDT', '4h'))
        return inf_pairs

    # ========== pytrendseries 趋势标签 ==========
    @staticmethod
    def compute_trend_labels(df_in: DataFrame, window: int, limit: int) -> Series:
        """
        使用 pytrendseries detecttrend 检测价格形态趋势。
        1=uptrend, -1=downtrend, 0=no_trend。
        接受 OHLCV dataframe 或单列 close dataframe，自动提取 close 和日期。
        """
        from pytrendseries import detecttrend

        df = df_in.copy()
        if isinstance(df, Series):
            df = df.to_frame('close')

        # 确保 DatetimeIndex，提取 close 列
        if not isinstance(df.index, pd.DatetimeIndex):
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
            else:
                df.index = pd.to_datetime(df.index)

        # 提取 close 列
        if 'close' in df.columns:
            close_col = 'close'
        else:
            close_col = df.columns[0]
        df_single = df[[close_col]]

        labels = Series(0, index=df.index, name='trend')

        old_stdout = sys.stdout
        sys.stdout = open(os.devnull, 'w')
        try:
            uptrends = detecttrend(df_single, trend='uptrend',
                                   window=window, limit=limit)
            if len(uptrends) > 0:
                for _, row in uptrends.iterrows():
                    mask = (df.index >= row['Valley Date']) & \
                           (df.index <= row['Peak Date'])
                    labels.loc[mask] = 1

            downtrends = detecttrend(df_single, trend='downtrend',
                                     window=window, limit=limit)
            if len(downtrends) > 0:
                for _, row in downtrends.iterrows():
                    mask = (df.index >= row['Peak Date']) & \
                           (df.index <= row['Valley Date'])
                    labels.loc[mask] = -1
        finally:
            sys.stdout = old_stdout

        return labels

    # ========== OTT 指标 (Vectorized) ==========
    def ott(self, dataframe: DataFrame, pds: int, percent: float, cmo_period: int):
        df = dataframe.copy()
        alpha = 2 / (pds + 1)

        df["ud1"] = np.where(
            df["close"] > df["close"].shift(1), (df["close"] - df["close"].shift()), 0.0
        )
        df["dd1"] = np.where(
            df["close"] < df["close"].shift(1), (df["close"].shift() - df["close"]), 0.0
        )
        df["UD"] = df["ud1"].rolling(cmo_period).sum()
        df["DD"] = df["dd1"].rolling(cmo_period).sum()
        df["CMO"] = ((df["UD"] - df["DD"]) / (df["UD"] + df["DD"]).replace(0, np.nan)).fillna(0).abs()

        var_vals = np.zeros(len(df), dtype=np.float64)
        close_vals = df["close"].values.astype(np.float64)
        cmo_vals = df["CMO"].values.astype(np.float64)
        for i in range(pds, len(df)):
            var_vals[i] = (alpha * cmo_vals[i] * close_vals[i]) + \
                          (1 - alpha * cmo_vals[i]) * var_vals[i - 1]
        df["Var"] = var_vals

        df["fark"] = df["Var"] * percent * 0.01
        df["newlongstop"] = df["Var"] - df["fark"]
        df["newshortstop"] = df["Var"] + df["fark"]

        longstop_vals = np.zeros(len(df), dtype=np.float64)
        shortstop_vals = np.full(len(df), np.inf, dtype=np.float64)
        for i in range(1, len(df)):
            if df["Var"].iat[i] > longstop_vals[i - 1]:
                longstop_vals[i] = max(df["newlongstop"].iat[i], longstop_vals[i - 1])
            else:
                longstop_vals[i] = df["newlongstop"].iat[i]
            if df["Var"].iat[i] < shortstop_vals[i - 1]:
                shortstop_vals[i] = min(df["newshortstop"].iat[i], shortstop_vals[i - 1])
            else:
                shortstop_vals[i] = df["newshortstop"].iat[i]
        df["longstop"] = longstop_vals
        df["shortstop"] = shortstop_vals

        df["xlongstop"] = np.where(
            ((df["Var"].shift(1) > df["longstop"].shift(1)) & (df["Var"] < df["longstop"].shift(1))),
            1, 0,
        ).astype(np.int64)
        df["xshortstop"] = np.where(
            ((df["Var"].shift(1) < df["shortstop"].shift(1)) & (df["Var"] > df["shortstop"].shift(1))),
            1, 0,
        ).astype(np.int64)

        df["trend"] = np.where(df["xshortstop"] == 1, 1,
                       np.where(df["xlongstop"] == 1, -1, np.nan))
        df["trend"] = df["trend"].ffill().fillna(1)
        df["dir"] = df["trend"].copy()

        df["MT"] = np.where(df["dir"] == 1, df["longstop"], df["shortstop"])
        df["OTT"] = np.where(
            df["Var"] > df["MT"],
            (df["MT"] * (200 + percent) / 200),
            (df["MT"] * (200 - percent) / 200),
        )
        df["OTT"] = df["OTT"].shift(2)

        return DataFrame(index=df.index, data={
            "OTT": df["OTT"], "VAR": df["Var"], "DIR": df["dir"],
        })

    def compute_ott_direction(self, dataframe: DataFrame):
        """计算 OTT 趋势方向: 1=看涨, -1=看空, 0=中性"""
        ott_data = self.ott(dataframe, self.ott_pds.value,
                            self.ott_percent.value, self.ott_cmo_period.value)
        direction = np.where(ott_data['VAR'] > ott_data['OTT'], 1,
                     np.where(ott_data['VAR'] < ott_data['OTT'], -1, 0))
        return direction

    # ========== 指标填充 ==========
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # 确保 DatetimeIndex（freqtrade 回测时使用 RangeIndex + date 列）
        # 保留 date 列以通过 validator 检查
        if not isinstance(dataframe.index, pd.DatetimeIndex) and 'date' in dataframe.columns:
            dataframe.index = pd.to_datetime(dataframe['date'])

        # 3m OTT (主周期，用于精确入场/出场时点)
        ott_3m = self.ott(dataframe, self.ott_pds.value,
                          self.ott_percent.value, self.ott_cmo_period.value)
        dataframe['ott'] = ott_3m['OTT']
        dataframe['var'] = ott_3m['VAR']
        dataframe['ott_dir'] = ott_3m['DIR']
        # 3m OTT 翻转（主周期入场信号）
        dataframe['ott_dir_3m_flip_up'] = (
            (dataframe['ott_dir'].shift(1) <= 0) & (dataframe['ott_dir'] == 1)
        ).astype(int)
        dataframe['ott_dir_3m_flip_down'] = (
            (dataframe['ott_dir'].shift(1) >= 0) & (dataframe['ott_dir'] == -1)
        ).astype(int)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['atr_ratio'] = dataframe['atr'] / dataframe['close']
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=20)
        dataframe['volume_spike'] = dataframe['volume'] / dataframe['volume_ma']
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)

        # === 15m: pytrendseries 趋势检测 + 大阴线/大阳线 ===
        inf_15m = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe='15m')
        if inf_15m is not None and len(inf_15m) >= 10:
            # 确保 DateTimeIndex
            df_15m = inf_15m.copy()
            if 'date' in df_15m.columns:
                df_15m['date'] = pd.to_datetime(df_15m['date'])
                df_15m.set_index('date', inplace=True)
            else:
                logger.warning(f"15m data for {metadata['pair']} has no 'date' column: "
                             f"cols={inf_15m.columns.tolist()}, idx_type={type(inf_15m.index)}")
            trend_15m = self.compute_trend_labels(
                df_15m, window=self.trend_15m_window.value, limit=8)
            dataframe['ott_dir_15m'] = trend_15m.reindex(
                dataframe.index, method='ffill').fillna(1)
        else:
            dataframe['ott_dir_15m'] = 1

        # 15m 大阴线/大阳线检测（用于 custom_exit）
        df_15m['atr'] = ta.ATR(df_15m, timeperiod=14)
        df_15m['big_bear'] = (
            (df_15m['close'] < df_15m['open']) &
            ((df_15m['open'] - df_15m['close']) > df_15m['atr'] * 0.8)
        ).astype(int)
        df_15m['big_bull'] = (
            (df_15m['close'] > df_15m['open']) &
            ((df_15m['close'] - df_15m['open']) > df_15m['atr'] * 0.8)
        ).astype(int)
        df_15m['consec_bear_15m'] = (
            df_15m['big_bear']
            .groupby((df_15m['big_bear'] == 0).cumsum())
            .cumsum()
        )
        df_15m['consec_bull_15m'] = (
            df_15m['big_bull']
            .groupby((df_15m['big_bull'] == 0).cumsum())
            .cumsum()
        )
        dataframe['consec_bear_15m'] = df_15m['consec_bear_15m'].reindex(
            dataframe.index, method='ffill').fillna(0).astype(int)
        dataframe['consec_bull_15m'] = df_15m['consec_bull_15m'].reindex(
            dataframe.index, method='ffill').fillna(0).astype(int)

        # === 1d: pytrendseries 趋势检测 ===
        inf_1d = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe='1d')
        if inf_1d is not None and len(inf_1d) >= 10:
            df_1d = inf_1d.copy()
            if 'date' in df_1d.columns:
                df_1d['date'] = pd.to_datetime(df_1d['date'])
                df_1d.set_index('date', inplace=True)
            trend_1d = self.compute_trend_labels(
                df_1d, window=self.trend_1d_window.value, limit=3)
            dataframe['ott_dir_1d'] = trend_1d.reindex(
                dataframe.index, method='ffill').fillna(1)
        else:
            dataframe['ott_dir_1d'] = 1

        # === 4h: pytrendseries 趋势检测 + 涨跌幅过滤 ===
        inf_4h = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe='4h')
        if inf_4h is not None and len(inf_4h) >= 10:
            df_4h = inf_4h.copy()
            if 'date' in df_4h.columns:
                df_4h['date'] = pd.to_datetime(df_4h['date'])
                df_4h.set_index('date', inplace=True)
            trend_4h = self.compute_trend_labels(
                df_4h, window=self.trend_4h_window.value, limit=5)
            dataframe['ott_dir_4h'] = trend_4h.reindex(
                dataframe.index, method='ffill').fillna(1)
        else:
            dataframe['ott_dir_4h'] = 1

        # 4h K线涨跌幅（用于过滤极端K线）
        df_4h['change_pct'] = (df_4h['close'] - df_4h['open']) / df_4h['open']
        dataframe['4h_change_pct'] = df_4h['change_pct'].reindex(
            dataframe.index, method='ffill').fillna(0)

        # === BTC 4h 方向（用于阻断做空） ===
        try:
            inf_btc = self.dp.get_pair_dataframe(pair='BTC/USDT:USDT', timeframe='4h')
            if inf_btc is not None and len(inf_btc) >= 10:
                df_btc = inf_btc.copy()
                if 'date' in df_btc.columns:
                    df_btc['date'] = pd.to_datetime(df_btc['date'])
                    df_btc.set_index('date', inplace=True)
                btc_trend = self.compute_trend_labels(
                    df_btc, window=60, limit=5)
                # 1=uptrend(bullish), -1/0=not bullish
                btc_dir = np.where(btc_trend == 1, 1, 0)
                dataframe['btc_bullish'] = Series(
                    btc_dir, index=btc_trend.index
                ).reindex(dataframe.index, method='ffill').fillna(0).astype(int)
            else:
                dataframe['btc_bullish'] = 1
        except Exception:
            dataframe['btc_bullish'] = 1

        return dataframe

    # ========== 入场 ==========
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        adx_ok = dataframe['adx'] > 25
        vol_ok = dataframe['volume_spike'] > 1.5

        # === 做多: 1d/4h/15m 看涨 + 3m 刚翻多（跌不动了）===
        tf_1d_bull = dataframe['ott_dir_1d'] == 1
        tf_4h_bull = dataframe['ott_dir_4h'] == 1
        tf_15m_bull = dataframe['ott_dir_15m'] == 1
        tf_3m_flip_up = dataframe['ott_dir_3m_flip_up'] == 1
        no_4h_crash = dataframe['4h_change_pct'] > -0.03

        long_conditions = (
            tf_1d_bull &
            tf_4h_bull &
            tf_15m_bull &
            tf_3m_flip_up &
            adx_ok &
            vol_ok &
            no_4h_crash
        )
        dataframe.loc[long_conditions, 'enter_long'] = 1

        # === 做空: 1d/4h/15m 看空 + 3m 刚翻空（涨不动了）+ BTC弱势 ===
        tf_1d_bear = dataframe['ott_dir_1d'] == -1
        tf_4h_bear = dataframe['ott_dir_4h'] == -1
        tf_15m_bear = dataframe['ott_dir_15m'] == -1
        tf_3m_flip_down = dataframe['ott_dir_3m_flip_down'] == 1
        btc_bear = dataframe['btc_bullish'] == 0
        no_4h_pump = dataframe['4h_change_pct'] < 0.03

        short_conditions = (
            tf_1d_bear &
            tf_4h_bear &
            tf_15m_bear &
            tf_3m_flip_down &
            btc_bear &
            adx_ok &
            vol_ok &
            no_4h_pump
        )
        dataframe.loc[short_conditions, 'enter_short'] = 1

        return dataframe

    # ========== 出场 ==========
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # 做多出场: 4h 趋势转空（大趋势反转，涨不动了）
        tf_4h_flip_bear = (
            (dataframe['ott_dir_4h'].shift(1) == 1) & (dataframe['ott_dir_4h'] == -1)
        )
        dataframe.loc[tf_4h_flip_bear, 'exit_long'] = 1

        # 做空出场: 4h 趋势转多（大趋势反转，跌不动了）
        tf_4h_flip_bull = (
            (dataframe['ott_dir_4h'].shift(1) == -1) & (dataframe['ott_dir_4h'] == 1)
        )
        dataframe.loc[tf_4h_flip_bull, 'exit_short'] = 1

        return dataframe

    # ========== 杠杆 ==========
    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return float(self.base_leverage_val.value)
        last = dataframe.iloc[-1].squeeze()
        atr_ratio = last.get('atr_ratio', 0.03)
        btc_bull = last.get('btc_bullish', 1)
        base_lev = float(self.base_leverage_val.value)
        max_lev = float(self.max_leverage_val.value)
        # 高波动降杠杆
        if atr_ratio > 0.08:
            return min(base_lev, max_leverage)
        # 1d/4h 双周期看涨 + BTC看涨 → 允许高杠杆
        tf_1d_bull = last.get('ott_dir_1d', 0) == 1
        tf_4h_bull = last.get('ott_dir_4h', 0) == 1
        if tf_1d_bull and tf_4h_bull and btc_bull:
            return min(max_lev, max_leverage)
        return min(base_lev, max_leverage)

    # ========== 仓位 ==========
    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            entry_tag: str, side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake
        last = dataframe.iloc[-1].squeeze()
        atr_ratio = last.get('atr_ratio', 0.03)
        btc_bull = last.get('btc_bullish', 1)
        # 波动率越高仓位越小
        if atr_ratio > 0.06:
            stake = proposed_stake * 0.3
        elif atr_ratio > 0.04:
            stake = proposed_stake * 0.5
        elif atr_ratio > 0.025:
            stake = proposed_stake * 0.7
        else:
            stake = proposed_stake
        # BTC弱势减半
        if not btc_bull:
            stake = stake * 0.5
        return max(min_stake, min(stake, max_stake))

    # ========== 动态出场 ==========
    def custom_exit(self, pair: str, trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        # 清算保护（最高优先级）
        leverage = trade.leverage if hasattr(trade, 'leverage') else 5.0
        if current_profit < -(1.0 / leverage) * 0.85:
            return 'liquidation_risk'

        # 亏损时不管，交给 stoploss / trailing stop / exit_trend 处理
        if current_profit <= 0:
            return None

        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None
        last = dataframe.iloc[-1].squeeze()

        # 做多: 15m 连续 3 根大阴线 — 加速下跌
        if last.get('consec_bear_15m', 0) >= 3:
            return 'big_bears_15m'

        # 做空: 15m 连续 3 根大阳线 — 加速反弹
        if last.get('consec_bull_15m', 0) >= 3:
            return 'big_bulls_15m'

        return None
