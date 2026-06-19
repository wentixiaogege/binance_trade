"""
SmallCapHunterV1 — 小币种趋势猎手 (v2: 滚动窗口消除未来信号)
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

v2 关键修复:
- process_only_new_candles = False: 回测中逐根K线推进，模拟实盘行为
- _trend_cache: 缓存pytrendseries结果，仅在高级别K线新增时重新计算
- 仅使用已完成的高级别K线(iloc[:-1])，避免未完成K线的噪声
- use_custom_stoploss = False: 启用杠杆动态止损
- adjust_entry_price: 首次入场不加偏移
"""

import logging
import os
import sys
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from pandas import DataFrame, Series
import talib.abstract as ta
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class SmallCapHunterV1(IStrategy):

    INTERFACE_VERSION: int = 3

    timeframe = '3m'
    startup_candle_count = 200

    # 风控
    minimal_roi = {"0": 0.12, "120": 0.08, "240": 0.05, "480": 0.03, "960": 0}
    trailing_stop = True
    trailing_stop_positive = 0.05
    trailing_stop_positive_offset = 0.08
    trailing_only_offset_is_reached = True
    use_custom_stoploss = False

    can_short = True
    # v2: 回测中逐根K线推进，消除pytrendseries全量数据的未来信号
    process_only_new_candles = False

    # 小币种必须market止损，limit止损跳空时不会成交
    order_types = {
        'entry': 'limit',
        'exit': 'limit',
        'emergency_exit': 'market',
        'stoploss': 'market',
        'stoploss_on_exchange': False,
    }
    max_open_trades = 5

    # === DCA加仓 (v3: 关闭，DCA在3m周期不可行) ===
    position_adjustment_enable = False
    max_entry_position_adjustment = 0
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
    stoploss = -0.30
    base_leverage_val = IntParameter(5, 10, default=5, space='buy')
    max_leverage_val = IntParameter(15, 30, default=30, space='buy')

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

    # === 滚动缓存: 避免每次调用都重算pytrendseries ===
    # {(pair, timeframe, window, limit): (last_closed_bar_time, trend_series)}
    _trend_cache: dict = {}
    _pair_loss_ring: dict = {}  # {pair: [bool,...]} 最近3笔是否止损
    _pair_jail_until: dict = {}  # {pair: timestamp} 禁闭到何时

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        inf_pairs = []
        for pair in pairs:
            inf_pairs.extend([
                (pair, '15m'), (pair, '4h'), (pair, '1d'),
            ])
        inf_pairs.append(('BTC/USDT:USDT', '4h'))
        return inf_pairs

    # ========== pytrendseries 趋势标签 (仅用已完成K线) ==========
    @staticmethod
    def compute_trend_labels(df_in: DataFrame, window: int, limit: int) -> Series:
        """
        使用 pytrendseries detecttrend 检测价格形态趋势。
        1=uptrend, -1=downtrend, 0=no_trend。
        仅使用已完成K线 (调用方应传入iloc[:-1]数据)。
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

    # ========== 带缓存的趋势计算 (v2: 避免重复调用detecttrend) ==========
    def _get_cached_trend(self, pair: str, timeframe: str,
                          df_htf: DataFrame, window: int, limit: int) -> Series:
        """
        获取趋势标签。仅使用已完成K线 (iloc[:-1])，缓存结果。
        只有当高级别K线新增时才重新计算 pytrendseries。
        """
        if len(df_htf) < 2:
            return Series(dtype=float)

        # 仅用已完成K线 (丢弃最后一根可能未完成的K线)
        df_completed = df_htf.iloc[:-1].copy()

        if len(df_completed) < 10:
            return Series(dtype=float)

        cache_key = (pair, timeframe, window, limit)
        last_closed_time = df_completed.index[-1]

        # 检查缓存: 最后一根已完成K线时间未变 → 复用
        if cache_key in self._trend_cache:
            cached_time, cached_trend = self._trend_cache[cache_key]
            if cached_time == last_closed_time:
                return cached_trend

        # 重新计算趋势
        trend = self.compute_trend_labels(df_completed, window=window, limit=limit)
        self._trend_cache[cache_key] = (last_closed_time, trend)
        return trend

    # ========== OTT 指标 (Vectorized) ==========
    def ott(self, dataframe: DataFrame, pds: int, percent: float, cmo_period: int):
        df = dataframe.copy()
        alpha = 2 / (pds + 1)

        df["ud1"] = np.where(
            df["close"] > df["close"].shift(1), (df["close"] - df["close"].shift(1)), 0.0
        )
        df["dd1"] = np.where(
            df["close"] < df["close"].shift(1), (df["close"].shift(1) - df["close"]), 0.0
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

        # 做多回调结束: OTT看涨≥5根 + 回调≥3阴线 + 阳线放量反弹
        dataframe['pullback_buy'] = (
            (dataframe['ott_dir'] == 1) &
            (dataframe['ott_bullish_bars'] >= 5) &
            (dataframe['bearish_bars'].shift(1) >= 3) &
            (dataframe['bullish_candle'] == 1) &
            (dataframe['volume_spike'] > 1.2)
        ).astype(int)
        # 做空反弹结束: OTT看空≥5根 + 反弹≥3阳线 + 阴线放量下跌
        dataframe['pullback_sell'] = (
            (dataframe['ott_dir'] == -1) &
            (dataframe['ott_bearish_bars'] >= 5) &
            (dataframe['bullish_bars'].shift(1) >= 3) &
            (dataframe['bearish_candle'] == 1) &
            (dataframe['volume_spike'] > 1.2)
        ).astype(int)

        # === 15m: pytrendseries 趋势检测 + 大阴线/大阳线 ===
        inf_15m = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe='15m')
        dataframe['consec_bear_15m'] = 0
        dataframe['consec_bull_15m'] = 0
        if inf_15m is not None and len(inf_15m) >= 10:
            df_15m = inf_15m.copy()
            if 'date' in df_15m.columns:
                df_15m['date'] = pd.to_datetime(df_15m['date'])
                df_15m.set_index('date', inplace=True)
            elif not isinstance(df_15m.index, pd.DatetimeIndex):
                logger.warning(f"15m data for {metadata['pair']} has no 'date' column: "
                             f"cols={inf_15m.columns.tolist()}, idx_type={type(inf_15m.index)}")

            # v2: 带缓存的趋势计算 (仅用已完成K线，无未来信号)
            trend_15m = self._get_cached_trend(
                metadata['pair'], '15m', df_15m,
                window=self.trend_15m_window.value, limit=8)
            if len(trend_15m) > 0:
                dataframe['ott_dir_15m'] = trend_15m.reindex(
                    dataframe.index, method='ffill').fillna(0)
            else:
                dataframe['ott_dir_15m'] = 0

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
        else:
            dataframe['ott_dir_15m'] = 0

        # === 1d: pytrendseries 趋势检测 ===
        inf_1d = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe='1d')
        if inf_1d is not None and len(inf_1d) >= 10:
            df_1d = inf_1d.copy()
            if 'date' in df_1d.columns:
                df_1d['date'] = pd.to_datetime(df_1d['date'])
                df_1d.set_index('date', inplace=True)

            # v2: 带缓存的趋势计算 (仅用已完成K线，无未来信号)
            trend_1d = self._get_cached_trend(
                metadata['pair'], '1d', df_1d,
                window=self.trend_1d_window.value, limit=3)
            if len(trend_1d) > 0:
                dataframe['ott_dir_1d'] = trend_1d.reindex(
                    dataframe.index, method='ffill').fillna(0)
            else:
                dataframe['ott_dir_1d'] = 0
        else:
            dataframe['ott_dir_1d'] = 0

        # === 4h: pytrendseries 趋势检测 + 涨跌幅过滤 ===
        inf_4h = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe='4h')
        dataframe['4h_change_pct'] = 0
        if inf_4h is not None and len(inf_4h) >= 10:
            df_4h = inf_4h.copy()
            if 'date' in df_4h.columns:
                df_4h['date'] = pd.to_datetime(df_4h['date'])
                df_4h.set_index('date', inplace=True)

            # v2: 带缓存的趋势计算 (仅用已完成K线，无未来信号)
            trend_4h = self._get_cached_trend(
                metadata['pair'], '4h', df_4h,
                window=self.trend_4h_window.value, limit=5)
            if len(trend_4h) > 0:
                dataframe['ott_dir_4h'] = trend_4h.reindex(
                    dataframe.index, method='ffill').fillna(0)
            else:
                dataframe['ott_dir_4h'] = 0

            # 4h K线涨跌幅（用于过滤极端K线）
            df_4h['change_pct'] = (df_4h['close'] - df_4h['open']) / df_4h['open']
            dataframe['4h_change_pct'] = df_4h['change_pct'].reindex(
                dataframe.index, method='ffill').fillna(0)
        else:
            dataframe['ott_dir_4h'] = 0

        # === BTC 4h 方向（用于阻断做空） ===
        try:
            inf_btc = self.dp.get_pair_dataframe(pair='BTC/USDT:USDT', timeframe='4h')
            if inf_btc is not None and len(inf_btc) >= 10:
                df_btc = inf_btc.copy()
                if 'date' in df_btc.columns:
                    df_btc['date'] = pd.to_datetime(df_btc['date'])
                    df_btc.set_index('date', inplace=True)

                # v2: 带缓存的趋势计算 (仅用已完成K线，无未来信号)
                btc_trend = self._get_cached_trend(
                    'BTC/USDT:USDT', '4h', df_btc, window=60, limit=5)
                if len(btc_trend) > 0:
                    btc_dir = np.where(btc_trend == 1, 1, 0)
                    dataframe['btc_bullish'] = Series(
                        btc_dir, index=btc_trend.index
                    ).reindex(dataframe.index, method='ffill').fillna(0).astype(int)
                else:
                    dataframe['btc_bullish'] = 0
            else:
                dataframe['btc_bullish'] = 0
        except Exception:
            dataframe['btc_bullish'] = 0

        return dataframe

    # ========== 入场 ==========
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        adx_ok = dataframe['adx'] > self.adx_threshold.value
        vol_ok = dataframe['volume_spike'] > self.volume_ratio.value
        change_lim = self.fourh_change_pct.value
        # 动量: 创10bar新高不空 / 创10bar新低不多
        no_new_high = dataframe['close'] <= dataframe['close'].rolling(10).max().shift(1)
        no_new_low = dataframe['close'] >= dataframe['close'].rolling(10).min().shift(1)

        # MTF共识: ≥2/3 周期方向一致才允许入场
        bullish_1d = (dataframe['ott_dir_1d'] == 1).astype(int)
        bullish_4h = (dataframe['ott_dir_4h'] == 1).astype(int)
        bullish_15m = (dataframe['ott_dir_15m'] == 1).astype(int)
        bullish_mtf = (bullish_1d + bullish_4h + bullish_15m) >= 2

        bearish_1d = (dataframe['ott_dir_1d'] == -1).astype(int)
        bearish_4h = (dataframe['ott_dir_4h'] == -1).astype(int)
        bearish_15m = (dataframe['ott_dir_15m'] == -1).astype(int)
        bearish_mtf = (bearish_1d + bearish_4h + bearish_15m) >= 2

        # 差币禁闭: 跳过被关禁闭的币
        pair = metadata['pair']
        if pair in self._pair_jail_until:
            if datetime.utcnow() < self._pair_jail_until[pair]:
                return dataframe  # 还在禁闭期, 不开新仓
            else:
                del self._pair_jail_until[pair]  # 禁闭到期, 释放

        # === 做多条件（2种入场类型：翻转 + 回调，互斥）===
        long_base = (
            bullish_mtf &
            (dataframe['4h_change_pct'] > -change_lim) &
            adx_ok & vol_ok
        )

        # Type A: OTT翻转入场 (要求15m同向，避免假突破)
        long_flip = long_base & (dataframe['ott_dir_3m_flip_up_recent'] == 1) & (dataframe['ott_dir_15m'] == 1)
        dataframe.loc[long_flip, 'enter_long'] = 1
        dataframe.loc[long_flip, 'enter_tag'] = 'flip_up'
        # 仅对最新K线打日志，避免历史信号刷屏
        if long_flip.iloc[-1]:
            i = dataframe.index[-1]
            logger.info(f"[ENTRY] {metadata['pair']} LONG flip_up "
                       f"1d={dataframe.at[i,'ott_dir_1d']} 4h={dataframe.at[i,'ott_dir_4h']} 15m={dataframe.at[i,'ott_dir_15m']} "
                       f"ott3m={dataframe.at[i,'ott_dir']} adx={dataframe.at[i,'adx']:.0f} rsi={dataframe.at[i,'rsi']:.0f} "
                       f"btc={dataframe.at[i,'btc_bullish']}")

        # Type B: 回调入场 (趋势已确立后回调结束)
        long_pullback = long_base & (dataframe['pullback_buy'] == 1) & ~long_flip
        dataframe.loc[long_pullback, 'enter_long'] = 1
        dataframe.loc[long_pullback, 'enter_tag'] = 'pullback_buy'
        if long_pullback.iloc[-1]:
            i = dataframe.index[-1]
            logger.info(f"[ENTRY] {metadata['pair']} LONG pullback_buy "
                       f"1d={dataframe.at[i,'ott_dir_1d']} 4h={dataframe.at[i,'ott_dir_4h']} 15m={dataframe.at[i,'ott_dir_15m']} "
                       f"ott3m={dataframe.at[i,'ott_dir']} adx={dataframe.at[i,'adx']:.0f} rsi={dataframe.at[i,'rsi']:.0f}")

        # === 做空条件（2种入场类型：翻转 + 反弹，互斥）===
        short_base = (
            bearish_mtf &
            (dataframe['4h_change_pct'] < change_lim) &
            (dataframe['btc_bullish'] == 0) &
            no_new_high &  # 不空强势上拉的币
            adx_ok & vol_ok
        )

        # Type A: OTT翻转入场 (要求15m同向，避免假突破)
        short_flip = short_base & (dataframe['ott_dir_3m_flip_down_recent'] == 1) & (dataframe['ott_dir_15m'] == -1)
        dataframe.loc[short_flip, 'enter_short'] = 1
        dataframe.loc[short_flip, 'enter_tag'] = 'flip_down'
        # 仅对最新K线打日志，避免历史信号刷屏
        if short_flip.iloc[-1]:
            i = dataframe.index[-1]
            logger.info(f"[ENTRY] {metadata['pair']} SHORT flip_down "
                       f"1d={dataframe.at[i,'ott_dir_1d']} 4h={dataframe.at[i,'ott_dir_4h']} 15m={dataframe.at[i,'ott_dir_15m']} "
                       f"ott3m={dataframe.at[i,'ott_dir']} adx={dataframe.at[i,'adx']:.0f} rsi={dataframe.at[i,'rsi']:.0f} "
                       f"btc={dataframe.at[i,'btc_bullish']}")

        # Type B: 反弹入场 (趋势已确立后反弹结束)
        short_pullback = short_base & (dataframe['pullback_sell'] == 1) & ~short_flip
        dataframe.loc[short_pullback, 'enter_short'] = 1
        dataframe.loc[short_pullback, 'enter_tag'] = 'pullback_sell'
        if short_pullback.iloc[-1]:
            i = dataframe.index[-1]
            logger.info(f"[ENTRY] {metadata['pair']} SHORT pullback_sell "
                       f"1d={dataframe.at[i,'ott_dir_1d']} 4h={dataframe.at[i,'ott_dir_4h']} 15m={dataframe.at[i,'ott_dir_15m']} "
                       f"ott3m={dataframe.at[i,'ott_dir']} adx={dataframe.at[i,'adx']:.0f} rsi={dataframe.at[i,'rsi']:.0f}")

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
        趋势方向上加仓: 首次入场按当前价，加仓挂单在更好价格
        做多: 回调时加仓，挂单在当前价下方
        做空: 反弹时加仓，挂单在当前价上方
        """
        step = float(self.dca_step.value)
        count = trade.nr_of_successful_entries  # 0=初始, 1=第一次加仓, 2=第二次加仓

        # v2: 首次入场不加偏移
        if count == 0:
            return rate

        if trade.is_short:
            adjusted = rate * (1 + step * count)
        else:
            adjusted = rate * (1 - step * count)

        return adjusted

    # ========== 杠杆: MTF共识越高杠杆越大 ==========
    LEV_MAP = {3: 30.0, 2: 20.0, 1: 10.0, 0: 5.0}

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return 2.0
        last = dataframe.iloc[-1].squeeze()
        atr_ratio = last.get('atr_ratio', 0.03)

        # 高波动降杠杆
        if atr_ratio > 0.08:
            return 2.0

        # 计算MTF共识度
        if side == 'long':
            mtf_cnt = (int(last.get('ott_dir_1d', 0) == 1) +
                       int(last.get('ott_dir_4h', 0) == 1) +
                       int(last.get('ott_dir_15m', 0) == 1))
        else:
            mtf_cnt = (int(last.get('ott_dir_1d', 0) == -1) +
                       int(last.get('ott_dir_4h', 0) == -1) +
                       int(last.get('ott_dir_15m', 0) == -1))

        return self.LEV_MAP.get(mtf_cnt, 5.0)

    # ========== 动态止损: 杠杆越高止损越紧，但必须在清算线之上留足缓冲 ==========
    def custom_stoploss(self, pair: str, trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        leverage = trade.leverage if hasattr(trade, 'leverage') else 5.0
        # 按杠杆等比缩放止损: 所有仓位最多亏1-1.5%价格波动
        if leverage >= 25:
            return -0.35
        elif leverage >= 15:
            return -0.30
        elif leverage >= 8:
            return -0.25
        else:
            return -0.20

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

        # 波动率越高仓位越小 (范围30-50 USDT)
        if atr_ratio > 0.06:
            stake = proposed_stake * 0.6
        elif atr_ratio > 0.04:
            stake = proposed_stake * 0.7
        elif atr_ratio > 0.025:
            stake = proposed_stake * 0.85
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

    # ========== 差币禁闭: 最近3笔有2笔止损 → 禁闭24h ==========
    def confirm_trade_exit(self, pair, trade, order_type, amount, rate,
                           time_in_force, exit_reason, **kwargs):
        is_loss = (exit_reason in ('stop_loss', 'liquidation_risk'))
        ring = self._pair_loss_ring.setdefault(pair, [])
        ring.append(is_loss)
        if len(ring) > 3:
            ring.pop(0)
        if ring.count(True) >= 2:
            self._pair_jail_until[pair] = datetime.utcnow().replace(hour=0, minute=0, second=0) + timedelta(days=1)
            logger.info(f"[JAIL] {pair} 禁闭24h (最近3笔: {ring})")
        return True  # 允许出场

    # ========== 动态出场 ==========
    def custom_exit(self, pair: str, trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        # 清算保护 (全仓下清算线很远, -50%才真正危险)
        if current_profit < -0.50:
            return 'liquidation_risk'

        # 亏损时交给 stoploss / trailing stop 处理
        if current_profit <= 0:
            return None

        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) < 2:
            return None
        last = dataframe.iloc[-1].squeeze()
        prev = dataframe.iloc[-2].squeeze()

        # === 3m OTT 翻转立即出场（趋势方向改变） ===
        if trade.is_short:
            if last.get('ott_dir', 0) == 1 and prev.get('ott_dir', 0) == -1:
                return 'ott_flip_short'
        else:
            if last.get('ott_dir', 0) == -1 and prev.get('ott_dir', 0) == 1:
                return 'ott_flip_long'

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
