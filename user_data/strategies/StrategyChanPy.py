"""
StrategyChanPy — 多周期BSP + 笔方向过滤 + 去重 + 确认
- 5m BSP检测 (CChan, 1000根窗口)
- 30m/4h/1d 笔方向过滤 (从5m合成, ≥2/3一致才允许)
- 同向信号去重 (间隔≥12根K线=1小时)
- 1根K线确认 + EMA50趋势过滤
- 优化参数: 10x杠杆, -8%止损, 6%激活/1.5%跟踪止盈
"""
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta

logger = logging.getLogger(__name__)

_CHAN_ROOT = Path(__file__).resolve().parent.parent.parent / "chan_lib"
if not _CHAN_ROOT.exists():
    _CHAN_ROOT = Path("/root/freqtrade_bot/chan_lib")
if str(_CHAN_ROOT) not in sys.path:
    sys.path.insert(0, str(_CHAN_ROOT))

from Chan import CChan
from ChanConfig import CChanConfig
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE, DATA_FIELD
from Common.CTime import CTime
from KLine.KLine_Unit import CKLine_Unit

_KL_ORDER = {KL_TYPE.K_30M: 5, KL_TYPE.K_4H: 7, KL_TYPE.K_DAY: 8}

CONFIG = CChanConfig({
    "bi_algo": "normal", "bi_strict": False, "trigger_step": True,
    "gap_as_kl": True, "one_bi_zs": True,
    "bi_fx_check": "strict", "bi_end_is_peak": True, "bi_allow_sub_peak": True,
    "zs_combine": True, "zs_combine_mode": "zs", "zs_algo": "normal",
    "divergence_rate": float("inf"), "min_zs_cnt": 0, "bs1_peak": False,
    "bsp1_only_multibi_zs": False, "max_bs2_rate": 0.9999,
    "macd_algo": "peak",
    "bs_type": "1,2,3a,1p,2s,3b",
    "bsp2_follow_1": False, "bsp3_follow_1": False,
    "bsp3_peak": False, "strict_bsp3": False, "bsp3a_max_zs_cnt": 3,
    "print_warning": False, "kl_data_check": False,
    "max_kl_misalgin_cnt": 999999, "max_kl_inconsistent_cnt": 999999,
})

# 高阶TF构建参数: 30m/4h从5m resample, 1d直接用交易所数据
MTF_CONFIG = [
    ("30min", KL_TYPE.K_30M, 300),
    ("4h",    KL_TYPE.K_4H,  300),
]


class StrategyChanPy(IStrategy):
    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "5m"

    # 止损用market订单，避免limit订单超时→雪球亏损
    order_types = {
        'entry': 'limit',
        'exit': 'limit',
        'stoploss': 'market',
        'stoploss_on_exchange': False,
    }

    LEV_MAP = {"mtf3": 30.0, "mtf2": 20.0, "mtf1": 10.0, "mtf0": 5.0}

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side) -> float:
        return self.LEV_MAP.get(entry_tag, 4.0)

    def informative_pairs(self):
        # 请求1d数据，用于日线笔方向（不复采样5m）
        pairs = self.dp.current_whitelist()
        return [(p, '1d') for p in pairs]

    stoploss = -0.30

    trailing_stop = True
    trailing_stop_positive = 0.08
    trailing_stop_positive_offset = 0.15
    trailing_only_offset_is_reached = True

    minimal_roi = {"0": 0.30, "120": 0.08, "360": 0.04, "720": 0.02, "1440": 0}

    process_only_new_candles = False
    use_exit_signal = True
    startup_candle_count = 2000

    _klu_cache = {}
    _klu_cache_max = 2000
    _last_buy_time = {}
    _last_sell_time = {}
    _bsp_cache = {}
    _mtf_cache = {}
    DEDUP_CANDLES = 12

    # —— KLU helpers ——

    def _klu_from_row(self, row):
        dt = row['date'].to_pydatetime()
        # feater文件可能带UTC时区, CTime只接受naive datetime
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return CKLine_Unit({
            DATA_FIELD.FIELD_TIME: CTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, auto=False),
            DATA_FIELD.FIELD_OPEN: float(row['open']),
            DATA_FIELD.FIELD_HIGH: float(row['high']),
            DATA_FIELD.FIELD_LOW: float(row['low']),
            DATA_FIELD.FIELD_CLOSE: float(row['close']),
            DATA_FIELD.FIELD_VOLUME: float(row['volume']),
        }, autofix=True)

    def _klu_list_from_df(self, df):
        return [self._klu_from_row(df.iloc[i]) for i in range(len(df))]

    def _klu_cache_to_df(self, pair, klus=None):
        """将KLU缓存转回DataFrame, 用于高阶TF resample. klus参数可选, 传入已完成K线列表."""
        if klus is None:
            klus = self._klu_cache.get(pair, [])
        rows = []
        for k in klus:
            t = k.time
            rows.append({
                'date': pd.Timestamp(
                    year=t.year, month=t.month, day=t.day,
                    hour=t.hour, minute=t.minute, tz='UTC'),
                'open': k.open, 'high': k.high, 'low': k.low,
                'close': k.close, 'volume': k.trade_info.metric.get('volume', 0),
            })
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    def _get_bi_direction(self, chan, kl_type):
        try:
            kl_list = chan[kl_type]
            if kl_list.bi_list and len(kl_list.bi_list) > 0:
                last_bi = kl_list.bi_list[-1]
                if last_bi.is_up():
                    return 1
                elif last_bi.is_down():
                    return -1
        except Exception:
            pass
        return 0

    # —— 高阶TF构建: 从5m dataframe 重采样 ——

    def _build_mtf_bi_directions(self, pair, dataframe_5m):
        """从5m数据合成30m/4h K线, 跑CChan获取笔方向. 返回 (bi_30m, bi_4h, success)."""
        bi_30m = bi_4h = 0
        success = False

        if len(dataframe_5m) < 120:
            return bi_30m, bi_4h, False

        df = dataframe_5m.copy()
        df['date'] = pd.to_datetime(df['date'], utc=True)
        df.set_index('date', inplace=True)

        for rule, kl_type, lookback in MTF_CONFIG:
            try:
                resampled = df.resample(rule, closed='right', label='right').agg({
                    'open': 'first', 'high': 'max', 'low': 'min',
                    'close': 'last', 'volume': 'sum',
                }).dropna()
            except Exception:
                continue

            if len(resampled) < 10:
                continue
            if len(resampled) > lookback:
                resampled = resampled.iloc[-lookback:]

            resampled = resampled.reset_index()
            tf_klus = self._klu_list_from_df(resampled)

            chan = CChan(code=pair, lv_list=[kl_type], config=CONFIG,
                         data_src=DATA_SRC.CCXT, autype=AUTYPE.QFQ)
            try:
                chan.trigger_load({kl_type: tf_klus})
            except Exception:
                continue

            direction = self._get_bi_direction(chan, kl_type)
            if kl_type == KL_TYPE.K_30M:
                bi_30m = direction
            elif kl_type == KL_TYPE.K_4H:
                bi_4h = direction
            success = True

        return bi_30m, bi_4h, success

    # —— main ——

    # —— 1d笔方向: 直接用交易所日线数据 ——
    _1d_cache = {}

    def _get_1d_bi_direction(self, pair):
        """用交易所1d数据跑CChan获取日线笔方向. 缓存结果."""
        inf_1d = self.dp.get_pair_dataframe(pair, '1d')
        if inf_1d is None or len(inf_1d) < 15:
            return 0
        df = inf_1d.copy()
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
        elif not isinstance(df.index, pd.DatetimeIndex):
            return 0

        # 只用已完成日线(丢弃最后一根未完成的)
        df_completed = df.iloc[:-1] if len(df) > 1 else df
        if len(df_completed) < 15:
            return 0

        last_idx = df_completed.index[-1]
        cache_key = (pair, last_idx)
        if cache_key in self._1d_cache:
            return self._1d_cache[cache_key]

        df_rst = df_completed.reset_index()
        klus = self._klu_list_from_df(df_rst)
        try:
            chan = CChan(code=pair, lv_list=[KL_TYPE.K_DAY], config=CONFIG,
                         data_src=DATA_SRC.CCXT, autype=AUTYPE.QFQ)
            chan.trigger_load({KL_TYPE.K_DAY: klus})
            direction = self._get_bi_direction(chan, KL_TYPE.K_DAY)
        except Exception:
            direction = 0

        self._1d_cache[cache_key] = direction
        if len(self._1d_cache) > 30:
            self._1d_cache.pop(min(self._1d_cache.keys()))
        return direction

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 安全: 重置索引 + 按时间升序(Chan库要求K线时间严格递增)
        dataframe = dataframe.reset_index(drop=True)
        dataframe['chan_buy'] = 0
        dataframe['chan_sell'] = 0
        dataframe['chan_tag'] = ''

        pair = metadata.get('pair', 'default')
        n = len(dataframe)

        # --- 5m KLU 缓存 (只从dataframe构建) ---
        if pair not in self._klu_cache:
            self._klu_cache[pair] = []

        df_sorted = dataframe.sort_values('date', ascending=True).reset_index(drop=True)
        cached_times = {
            (k.time.year, k.time.month, k.time.day, k.time.hour, k.time.minute)
            for k in self._klu_cache[pair]
        }

        new_klus = 0
        for _, row in df_sorted.iterrows():
            dt = row['date'].to_pydatetime()
            tk = (dt.year, dt.month, dt.day, dt.hour, dt.minute)
            if tk not in cached_times:
                self._klu_cache[pair].append(self._klu_from_row(row))
                cached_times.add(tk)
                new_klus += 1

        if len(self._klu_cache[pair]) > self._klu_cache_max:
            self._klu_cache[pair] = self._klu_cache[pair][-self._klu_cache_max:]

        all_klus_5m = self._klu_cache[pair]

        # EMA50必须在所有early return之前计算, 避免populate_entry_trend KeyError
        dataframe['ema50'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)

        if len(all_klus_5m) < 10:
            return dataframe

        # --- 5m BSP检测 (缓存: 只有新K线才重算, 用末尾K线时间防碰撞)
        # 🔧 仅用已完成K线[:-1], 消除回测/实盘差异 (最后一根在实盘中未完成, 与回测完整K线不一致)
        last_klu = all_klus_5m[-1]
        last_klu_tk = (last_klu.time.year, last_klu.time.month, last_klu.time.day,
                       last_klu.time.hour, last_klu.time.minute)
        completed_klus = all_klus_5m[:-1] if len(all_klus_5m) > 1 else all_klus_5m
        cache_key = (pair, len(completed_klus), last_klu_tk)
        if cache_key in self._bsp_cache:
            buy, sell = self._bsp_cache[cache_key]
        else:
            chan_5m = CChan(code=pair, lv_list=[KL_TYPE.K_5M], config=CONFIG,
                            data_src=DATA_SRC.CCXT, autype=AUTYPE.QFQ)
            try:
                chan_5m.trigger_load({KL_TYPE.K_5M: completed_klus})
            except Exception:
                return dataframe

            bsp_list = chan_5m.get_latest_bsp(number=0)
            buy = {}
            sell = {}
            for bsp in bsp_list:
                t = bsp.klu.time
                tk = (t.year, t.month, t.day, t.hour, t.minute)
                if bsp.is_buy:
                    buy[tk] = True
                else:
                    sell[tk] = True

            self._bsp_cache[cache_key] = (buy, sell)
            # 限制缓存大小
            if len(self._bsp_cache) > 50:
                self._bsp_cache.pop(min(self._bsp_cache.keys()))

        # --- 高阶TF笔方向 (30m/4h从5m合成, 1d用交易所数据) ---
        mtf_cache_key = (pair, len(completed_klus), last_klu_tk)
        if mtf_cache_key in self._mtf_cache:
            bi_30m, bi_4h, bi_1d, mtf_ok = self._mtf_cache[mtf_cache_key]
        else:
            full_df_5m = self._klu_cache_to_df(pair, klus=completed_klus)
            bi_30m, bi_4h, mtf_ok = self._build_mtf_bi_directions(pair, full_df_5m)
            # 1d: 直接用交易所日线数据 (不复采样5m, 数据足够CChan形成笔)
            bi_1d = self._get_1d_bi_direction(pair)
            self._mtf_cache[mtf_cache_key] = (bi_30m, bi_4h, bi_1d, mtf_ok)
            if len(self._mtf_cache) > 50:
                self._mtf_cache.pop(min(self._mtf_cache.keys()))

        # 存入dataframe供日志使用
        dataframe['bi_30m'] = bi_30m
        dataframe['bi_4h'] = bi_4h
        dataframe['bi_1d'] = bi_1d

        # 方向过滤: ≥2/3 (30m+4h+1d). MTF失败时不做过滤(保守), 但标记为mtf0低质量
        buy_support = 0
        sell_support = 0
        if mtf_ok:
            buy_support = (bi_4h == 1) + (bi_30m == 1) + (bi_1d == 1)
            sell_support = (bi_4h == -1) + (bi_30m == -1) + (bi_1d == -1)
            allow_buy = (buy_support >= 2)
            allow_sell = (sell_support >= 2)
        else:
            # MTF计算失败时, 只用1d笔方向过滤 (单周期保底)
            allow_buy = (bi_1d == 1)
            allow_sell = (bi_1d == -1)
            buy_support = 1 if bi_1d == 1 else 0
            sell_support = 1 if bi_1d == -1 else 0

        # 🔧 诊断日志: 每2小时(24次5m调用)输出一次，查0交易根因
        _diag_interval = 24  # 2h
        if not hasattr(self, '_diag_call_count'):
            self._diag_call_count = {}
        self._diag_call_count[pair] = self._diag_call_count.get(pair, 0) + 1
        _do_diag = (self._diag_call_count[pair] == 1) or (self._diag_call_count[pair] % _diag_interval == 0)
        if _do_diag:
            logger.info(f"[DIAG] {pair} buy_pts={len(buy)} sell_pts={len(sell)} "
                       f"bi(30m={bi_30m} 4h={bi_4h} 1d={bi_1d}) mtf_ok={mtf_ok} "
                       f"allow(buy={allow_buy} sell={allow_sell}) "
                       f"dedup_buy={'set' if pair in self._last_buy_time else 'no'} "
                       f"dedup_sell={'set' if pair in self._last_sell_time else 'no'}")

        # --- 去重: 同向信号间隔≥12根5m K线(1小时), 用datetime时间差 ---
        dedup_seconds = self.DEDUP_CANDLES * 5 * 60  # 3600s = 1h

        # 跳过最后1根K线 — BSP基于未完成结构, 可能变化
        stable_cutoff = n - 1

        for i, row in dataframe.iterrows():
            if i >= stable_cutoff:
                continue
            dt_ts = row['date'].to_pydatetime()
            # 统一去除时区: feather文件可能带UTC时区, 但CTime是naive
            if dt_ts.tzinfo is not None:
                dt_ts = dt_ts.replace(tzinfo=None)
            tk = (dt_ts.year, dt_ts.month, dt_ts.day, dt_ts.hour, dt_ts.minute)

            buy_allowed = True
            sell_allowed = True
            if pair in self._last_buy_time:
                lb = self._last_buy_time[pair]
                last_dt = datetime(lb.year, lb.month, lb.day, lb.hour, lb.minute)
                buy_allowed = (dt_ts - last_dt).total_seconds() >= dedup_seconds
            if pair in self._last_sell_time:
                ls = self._last_sell_time[pair]
                last_dt = datetime(ls.year, ls.month, ls.day, ls.hour, ls.minute)
                sell_allowed = (dt_ts - last_dt).total_seconds() >= dedup_seconds

            if tk in buy and allow_buy and buy_allowed:
                dataframe.at[i, 'chan_buy'] = 1
                dataframe.at[i, 'chan_tag'] = f"mtf{buy_support}"
                self._last_buy_time[pair] = CTime(dt_ts.year, dt_ts.month, dt_ts.day, dt_ts.hour, dt_ts.minute, auto=False)

            if tk in sell and allow_sell and sell_allowed:
                dataframe.at[i, 'chan_sell'] = 1
                if not dataframe.at[i, 'chan_tag']:
                    dataframe.at[i, 'chan_tag'] = f"mtf{sell_support}"
                self._last_sell_time[pair] = CTime(dt_ts.year, dt_ts.month, dt_ts.day, dt_ts.hour, dt_ts.minute, auto=False)

        # 🔧 诊断: 循环后统计chan_buy/chan_sell数量
        if _do_diag:
            n_cb = int(dataframe['chan_buy'].sum())
            n_cs = int(dataframe['chan_sell'].sum())
            last_idx = n - 1
            # 看最近20根K线 (倒数第二是最后一根有效K线)
            tail_start = max(0, last_idx - 20)
            logger.info(f"[DIAG-LOOP] {pair} chan_buy={n_cb} chan_sell={n_cs} "
                       f"tail20_buy={int(dataframe['chan_buy'].iloc[tail_start:last_idx].sum())} "
                       f"tail20_sell={int(dataframe['chan_sell'].iloc[tail_start:last_idx].sum())} "
                       f"df_rows={n} cache_klus={len(all_klus_5m)} completed={len(completed_klus)}")

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        bull = dataframe['close'] > dataframe['ema50']
        bear = ~bull
        trend_ok = dataframe['adx'] > 15
        no_new_high = dataframe['close'] <= dataframe['close'].rolling(10).max().shift(1)
        no_new_low = dataframe['close'] >= dataframe['close'].rolling(10).min().shift(1)

        price_up = dataframe['close'] > dataframe['close'].shift(1)
        price_down = dataframe['close'] < dataframe['close'].shift(1)
        # BSP时效+无冲突: 6根K线内有同向BSP, 且当前没有反向BSP
        recent_buy = (dataframe['chan_buy'].rolling(6).max() == 1) & (dataframe['chan_sell'].rolling(6).max() == 0)
        recent_sell = (dataframe['chan_sell'].rolling(6).max() == 1) & (dataframe['chan_buy'].rolling(6).max() == 0)

        # v2: 不要求BSP恰好在上一根K线, 用recent_buy(6根内)即可
        long_matches = dataframe[recent_buy & price_up & bull & trend_ok & no_new_low & (dataframe['volume'] > 0)]
        for i in long_matches.index:
            dataframe.at[i, 'enter_long'] = 1
            dataframe.at[i, 'enter_tag'] = 'recent_bsp'
        if len(long_matches) > 0:
            i = long_matches.index[-1]
            logger.info(f"[ENTRY] {metadata['pair']} LONG "
                       f"bi_30m={dataframe.at[i,'bi_30m']} bi_4h={dataframe.at[i,'bi_4h']} bi_1d={dataframe.at[i,'bi_1d']}")

        short_matches = dataframe[recent_sell & price_down & bear & trend_ok & no_new_high & (dataframe['volume'] > 0)]
        for i in short_matches.index:
            dataframe.at[i, 'enter_short'] = 1
            dataframe.at[i, 'enter_tag'] = 'recent_bsp'
        if len(short_matches) > 0:
            i = short_matches.index[-1]
            logger.info(f"[ENTRY] {metadata['pair']} SHORT "
                       f"bi_30m={dataframe.at[i,'bi_30m']} bi_4h={dataframe.at[i,'bi_4h']} bi_1d={dataframe.at[i,'bi_1d']}")

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ex_l = (dataframe['chan_sell'] == 1) & (dataframe['volume'] > 0)
        ex_s = (dataframe['chan_buy'] == 1) & (dataframe['volume'] > 0)
        dataframe.loc[ex_l, 'exit_long'] = 1
        dataframe.loc[ex_s, 'exit_short'] = 1
        n = len(dataframe)
        if ex_l.any() and dataframe[ex_l].index[-1] == n - 1:
            logger.info(f"[EXIT] {metadata['pair']} LONG exit_signal "
                       f"bi_30m={dataframe.at[n-1,'bi_30m']} bi_4h={dataframe.at[n-1,'bi_4h']} bi_1d={dataframe.at[n-1,'bi_1d']}")
        if ex_s.any() and dataframe[ex_s].index[-1] == n - 1:
            logger.info(f"[EXIT] {metadata['pair']} SHORT exit_signal "
                       f"bi_30m={dataframe.at[n-1,'bi_30m']} bi_4h={dataframe.at[n-1,'bi_4h']} bi_1d={dataframe.at[n-1,'bi_1d']}")
        return dataframe

    def custom_entry_price(self, pair, current_time, proposed_rate, entry_tag=None, side=None, **kwargs):
        # 做多: 略低价买入, 做空: 略高价卖出
        return proposed_rate * 0.99985 if side == 'long' else proposed_rate * 1.00015

    def custom_exit_price(self, pair, current_time, proposed_rate, entry_tag=None, side=None, **kwargs):
        # 做多: 略高价卖出, 做空: 略低价买回 (与入场对称, 无方向偏差)
        return proposed_rate * 1.00015 if side == 'long' else proposed_rate * 0.99985
