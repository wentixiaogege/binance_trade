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
    trailing_stop = True
    trailing_stop_positive = 0.08
    trailing_stop_positive_offset = 0.12
    trailing_only_offset_is_reached = False
    use_custom_stoploss = False

    can_short = True
    process_only_new_candles = True
    max_open_trades = 5

    # === 马丁DCA加仓 ===
    position_adjustment_enable = True
    max_entry_position_adjustment = 2  # 最多加仓2次（共3次入场）
    dca_step = DecimalParameter(0.02, 0.06, default=0.03, space='buy')  # 每次加仓间距

    protections = [
        {"method": "CooldownPeriod", "stop_duration_candles": 4},
        {"method": "StoplossGuard", "lookback_period_candles": 24,
         "trade_limit": 4, "stop_duration_candles": 12},
        {"method": "MaxDrawdown", "lookback_period_candles": 48,
         "trade_limit": 20, "stop_duration_candles": 24,
         "max_allowed_drawdown": 0.20},
    ]

    # === 风控 ===
    stoploss = -0.125
    base_leverage_val = IntParameter(2, 5, default=2, space='buy')
    max_leverage_val = IntParameter(3, 8, default=5, space='buy')

    # === OTT 超参 ===
    ott_percent = DecimalParameter(1.0, 2.5, default=1.883, space='buy')
    ott_pds = IntParameter(2, 4, default=3, space='buy')
    ott_cmo_period = IntParameter(7, 12, default=12, space='buy')

    # === pytrendseries 超参 ===
    trend_1d_window = IntParameter(20, 40, default=31, space='buy')
    trend_4h_window = IntParameter(40, 80, default=44, space='buy')
    trend_15m_window = IntParameter(128, 256, default=240, space='buy')

    # === 入场过滤超参 ===
    adx_threshold = IntParameter(12, 35, default=22, space='buy')
    volume_ratio = DecimalParameter(1.0, 3.0, default=1.3, space='buy')
    fourh_change_pct = DecimalParameter(0.01, 0.06, default=0.021, space='buy')

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
        var_arr = df["Var"].values
        nls_arr = df["newlongstop"].values
        nss_arr = df["newshortstop"].values
        for i in range(1, len(df)):
            if var_arr[i] > longstop_vals[i - 1]:
                longstop_vals[i] = max(nls_arr[i], longstop_vals[i - 1])
            else:
                longstop_vals[i] = nls_arr[i]
            if var_arr[i] < shortstop_vals[i - 1]:
                shortstop_vals[i] = min(nss_arr[i], shortstop_vals[i - 1])
            else:
                shortstop_vals[i] = nss_arr[i]
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
        # Recent flip: flipped within last 2 candles + still in same direction
        dataframe['ott_dir_3m_flip_up_recent'] = (
            (dataframe['ott_dir_3m_flip_up'].rolling(2).max() >= 1) &
            (dataframe['ott_dir'] == 1)
        ).astype(int)
        dataframe['ott_dir_3m_flip_down_recent'] = (
            (dataframe['ott_dir_3m_flip_down'].rolling(2).max() >= 1) &
            (dataframe['ott_dir'] == -1)
        ).astype(int)
        # OTT 同向持续K线数（衡量趋势/回调深度）
        dataframe['ott_bullish'] = (dataframe['ott_dir'] == 1).astype(int)
        dataframe['ott_bullish_bars'] = (
            dataframe['ott_bullish']
            .groupby((dataframe['ott_bullish'] == 0).cumsum()).cumsum()
        )
        dataframe['ott_bearish'] = (dataframe['ott_dir'] == -1).astype(int)
        dataframe['ott_bearish_bars'] = (
            dataframe['ott_bearish']
            .groupby((dataframe['ott_bearish'] == 0).cumsum()).cumsum()
        )
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['atr_ratio'] = dataframe['atr'] / dataframe['close']
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=20)
        dataframe['volume_spike'] = dataframe['volume'] / dataframe['volume_ma']
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['sma_20'] = ta.SMA(dataframe, timeperiod=20)

        # 价格方向蜡烛定义（先定义，后续 exhaustion/pullback/counter 都要用）
        dataframe['bearish_candle'] = (
            dataframe['close'] < dataframe['close'].shift(1)
        ).astype(int)
        dataframe['bearish_bars'] = (
            dataframe['bearish_candle']
            .groupby((dataframe['bearish_candle'] == 0).cumsum()).cumsum()
        )
        dataframe['bullish_candle'] = (
            dataframe['close'] > dataframe['close'].shift(1)
        ).astype(int)
        dataframe['bullish_bars'] = (
            dataframe['bullish_candle']
            .groupby((dataframe['bullish_candle'] == 0).cumsum()).cumsum()
        )

        # === Exhaustion 检测指标 ===
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # Bollinger Bands (用于判断价格是否在极端位置)
        bb = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_lower'] = bb['lowerband']
        dataframe['bb_upper'] = bb['upperband']
        dataframe['bb_mid'] = bb['middleband']

        # 成交量衰竭：连续阴线/阳线中量能递减（卖盘/买盘枯竭）
        dataframe['vol_3bar_avg'] = dataframe['volume'].rolling(3).mean().shift(1)
        dataframe['sell_exhaustion'] = (
            (dataframe['bearish_candle'] == 1) &
            (dataframe['volume'] < dataframe['vol_3bar_avg'] * 0.8) &
            (dataframe['volume'].shift(1) < dataframe['volume'].shift(2))
        ).astype(int)
        dataframe['buy_exhaustion'] = (
            (dataframe['bullish_candle'] == 1) &
            (dataframe['volume'] < dataframe['vol_3bar_avg'] * 0.8) &
            (dataframe['volume'].shift(1) < dataframe['volume'].shift(2))
        ).astype(int)
        # 做多回调结束: OTT看涨 + 刚经历>=3根阴线回调 + 当前阳线反弹 + 放量确认
        dataframe['pullback_buy'] = (
            (dataframe['ott_dir'] == 1) &
            (dataframe['bearish_bars'].shift(1) >= 3) &
            (dataframe['bullish_candle'] == 1) &
            (dataframe['volume_spike'] > 1.0)
        ).astype(int)
        # 做空反弹结束: OTT看空 + 刚经历>=3根阳线反弹 + 当前阴线下跌 + 放量确认
        dataframe['pullback_sell'] = (
            (dataframe['ott_dir'] == -1) &
            (dataframe['bullish_bars'].shift(1) >= 3) &
            (dataframe['bearish_candle'] == 1) &
            (dataframe['volume_spike'] > 1.0)
        ).astype(int)

        # 抄底做多: 3m OTT偏空 + 连续下跌后卖盘枯竭 + RSI超卖 + 价格在布林下轨附近
        dataframe['counter_buy'] = (
            (dataframe['ott_dir'] == -1) &
            (dataframe['bearish_bars'].shift(1) >= 5) &
            (dataframe['bullish_candle'] == 1) &
            (dataframe['rsi'] < 40) &
            (dataframe['close'] <= dataframe['bb_lower'] * 1.03) &
            (dataframe['sell_exhaustion'].rolling(3).max() >= 1) &
            (dataframe['volume_spike'] > 1.1)
        ).astype(int)
        # 摸顶做空: 3m OTT偏多 + 连续上涨后买盘枯竭 + RSI超买 + 价格在布林上轨附近
        dataframe['counter_sell'] = (
            (dataframe['ott_dir'] == 1) &
            (dataframe['bullish_bars'].shift(1) >= 5) &
            (dataframe['bearish_candle'] == 1) &
            (dataframe['rsi'] > 60) &
            (dataframe['close'] >= dataframe['bb_upper'] * 0.97) &
            (dataframe['buy_exhaustion'].rolling(3).max() >= 1) &
            (dataframe['volume_spike'] > 1.1)
        ).astype(int)

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

        adx_ok = dataframe['adx'] > self.adx_threshold.value
        vol_ok = dataframe['volume_spike'] > self.volume_ratio.value
        change_lim = self.fourh_change_pct.value

        # === 做多条件（分离三种入场类型，各自打tag）===
        long_base = (
            (dataframe['ott_dir_1d'] != -1) &
            (dataframe['ott_dir_4h'] != -1) &
            (dataframe['ott_dir_15m'] != -1) &
            (dataframe['4h_change_pct'] > -change_lim) &
            adx_ok & vol_ok
        )

        # Type A: OTT翻转入场
        long_flip = long_base & (dataframe['ott_dir_3m_flip_up_recent'] == 1)
        dataframe.loc[long_flip, 'enter_long'] = 1
        dataframe.loc[long_flip, 'enter_tag'] = 'flip_up'

        # Type B: 回调入场
        long_pullback = long_base & (dataframe['pullback_buy'] == 1) & ~long_flip
        dataframe.loc[long_pullback, 'enter_long'] = 1
        dataframe.loc[long_pullback, 'enter_tag'] = 'pullback_buy'

        # Type C: 逆势抄底（exhaustion点）
        long_counter = long_base & (dataframe['counter_buy'] == 1) & ~long_flip & ~long_pullback
        dataframe.loc[long_counter, 'enter_long'] = 1
        dataframe.loc[long_counter, 'enter_tag'] = 'counter_buy'

        # Type D: DCA加仓（趋势方向上价格回调时持续加仓）
        # 不要求 adx_ok + vol_ok，比首发信号更宽松，确保加仓窗口持续打开
        long_dca = (
            (dataframe['ott_dir_1d'] != -1) &
            (dataframe['ott_dir_4h'] != -1) &
            (dataframe['ott_dir_15m'] != -1) &
            (dataframe['ott_dir'] == 1) &
            (dataframe['close'] < dataframe['sma_20']) &
            ~long_flip & ~long_pullback & ~long_counter
        )
        dataframe.loc[long_dca, 'enter_long'] = 1
        dataframe.loc[long_dca, 'enter_tag'] = 'dca_long'

        # === 做空条件（分离三种入场类型）===
        short_base = (
            (dataframe['ott_dir_1d'] != 1) &
            (dataframe['ott_dir_4h'] != 1) &
            (dataframe['ott_dir_15m'] != 1) &
            (dataframe['4h_change_pct'] < change_lim) &
            (dataframe['btc_bullish'] == 0) &
            adx_ok & vol_ok
        )

        # Type A: OTT翻转入场
        short_flip = short_base & (dataframe['ott_dir_3m_flip_down_recent'] == 1)
        dataframe.loc[short_flip, 'enter_short'] = 1
        dataframe.loc[short_flip, 'enter_tag'] = 'flip_down'

        # Type B: 反弹入场
        short_pullback = short_base & (dataframe['pullback_sell'] == 1) & ~short_flip
        dataframe.loc[short_pullback, 'enter_short'] = 1
        dataframe.loc[short_pullback, 'enter_tag'] = 'pullback_sell'

        # Type C: 逆势摸顶（exhaustion点）
        short_counter = short_base & (dataframe['counter_sell'] == 1) & ~short_flip & ~short_pullback
        dataframe.loc[short_counter, 'enter_short'] = 1
        dataframe.loc[short_counter, 'enter_tag'] = 'counter_sell'

        # Type D: DCA加仓（趋势方向上价格反弹时持续加仓）
        short_dca = (
            (dataframe['ott_dir_1d'] != 1) &
            (dataframe['ott_dir_4h'] != 1) &
            (dataframe['ott_dir_15m'] != 1) &
            (dataframe['ott_dir'] == -1) &
            (dataframe['close'] > dataframe['sma_20']) &
            ~short_flip & ~short_pullback & ~short_counter
        )
        dataframe.loc[short_dca, 'enter_short'] = 1
        dataframe.loc[short_dca, 'enter_tag'] = 'dca_short'

        return dataframe

    # ========== 出场 ==========
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # 4h 趋势翻转（大级别反转，保底出场）
        tf_4h_flip_bear = (
            (dataframe['ott_dir_4h'].shift(1) == 1) & (dataframe['ott_dir_4h'] == -1)
        )
        dataframe.loc[tf_4h_flip_bear, 'exit_long'] = 1

        tf_4h_flip_bull = (
            (dataframe['ott_dir_4h'].shift(1) == -1) & (dataframe['ott_dir_4h'] == 1)
        )
        dataframe.loc[tf_4h_flip_bull, 'exit_short'] = 1

        return dataframe

    # ========== DCA 马丁加仓 ==========
    def adjust_entry_price(self, trade, order_type, amount, rate,
                           time_in_force, side, entry_tag, **kwargs):
        """
        趋势方向上加仓: 每次加仓挂单在更好价格
        做多: 回调时加仓，挂单在当前价下方
        做空: 反弹时加仓，挂单在当前价上方
        """
        step = float(self.dca_step.value)
        count = trade.nr_of_successful_entries  # 0=初始, 1=第一次加仓, 2=第二次加仓

        if trade.is_short:
            adjusted = rate * (1 + step * (count + 1))
        else:
            adjusted = rate * (1 - step * (count + 1))

        return adjusted

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

        # 检查是否已有持仓（DCA加仓）
        trade = kwargs.get('trade', None)
        is_dca = trade is not None and trade.nr_of_successful_entries > 0

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

        # DCA加仓: 越加越大（马丁）
        if is_dca:
            dca_mult = 1.5 ** trade.nr_of_successful_entries
            stake = stake * dca_mult

        return max(min_stake, min(stake, max_stake))

    # ========== 动态出场 ==========
    def custom_exit(self, pair: str, trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        # 清算保护（最高优先级）
        leverage = trade.leverage if hasattr(trade, 'leverage') else 5.0
        if current_profit < -(1.0 / leverage) * 0.85:
            return 'liquidation_risk'

        # 亏损时交给 stoploss / trailing stop 处理
        if current_profit <= 0:
            return None

        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) < 2:
            return None
        last = dataframe.iloc[-1].squeeze()
        prev = dataframe.iloc[-2].squeeze()

        enter_tag = getattr(trade, 'enter_tag', '')

        # === Counter-trend 快进快出：达到目标利润就跑 ===
        if enter_tag in ('counter_buy', 'counter_sell'):
            # 如果 OTT 已经翻转同向 → 升级为趋势交易，不提前退出
            if enter_tag == 'counter_buy' and last.get('ott_dir', 0) == 1:
                pass  # OTT翻多了，继续持有用正常出场逻辑
            elif enter_tag == 'counter_sell' and last.get('ott_dir', 0) == -1:
                pass  # OTT翻空了，继续持有
            else:
                # 逆势交易，快进快出
                if current_profit > 0.04:
                    return 'counter_tp'
                # 反弹到布林中轨也走
                if enter_tag == 'counter_buy' and last.get('close', 0) >= last.get('bb_mid', 999999):
                    return 'counter_bb_mid'
                if enter_tag == 'counter_sell' and last.get('close', 0) <= last.get('bb_mid', 0):
                    return 'counter_bb_mid'

        if trade.is_short:
            # === 做空出场: 跌到尽头 ===
            if (last.get('ott_dir_3m_flip_up', 0) == 1 and
                    prev.get('ott_bearish_bars', 0) >= 3 and
                    last.get('ott_dir_15m', 0) != -1 and
                    last.get('volume_spike', 1.0) < 1.0):
                return 'exhaustion_short'

            if last.get('consec_bull_15m', 0) >= 3:
                return 'big_bulls_15m'
        else:
            # === 做多出场: 涨到尽头 ===
            if (last.get('ott_dir_3m_flip_down', 0) == 1 and
                    prev.get('ott_bullish_bars', 0) >= 3 and
                    last.get('ott_dir_15m', 0) != 1 and
                    last.get('volume_spike', 1.0) < 1.0):
                return 'exhaustion_long'

            if last.get('consec_bear_15m', 0) >= 3:
                return 'big_bears_15m'

        return None
