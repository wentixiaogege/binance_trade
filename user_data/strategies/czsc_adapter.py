"""
czsc_adapter.py — 基于 czsc 库的 Freqtrade 适配器 (v4)

使用 czsc 内置信号逻辑检测缠论买卖点：
- cxt_first_buy_V221126 / cxt_first_sell_V221126 (一买/一卖)
- cxt_second_bs_V240524 (二买/二卖 — 中枢重叠确认)
- cxt_bs_V240527 (趋势跟随买/卖点)

v4 改动：新增二买/二卖信号 (中枢视角下的并列二买/二卖)
"""

import pandas as pd
import numpy as np
from typing import List, Optional

from czsc import CZSC, Freq, RawBar
from czsc import format_standard_kline
from czsc.utils.sig import get_sub_elements


# ─── 工具函数 ─────────────────────────────────────────

def _bi_price_power(bi) -> float:
    pp = getattr(bi, 'power_price', 0) or 0
    if pp > 0:
        return pp
    change = abs(getattr(bi, 'change', 0) or 0)
    if change > 0:
        return change
    low = getattr(bi, 'low', 0)
    if low > 0:
        return (getattr(bi, 'high', 0) - low) / low
    return 0


def _ubi_power_ratio(ubi, current_price: float) -> float:
    if not ubi:
        return 0
    hp = ubi.get("high_bar")
    lp = ubi.get("low_bar")
    if hp is None or lp is None or current_price <= 0:
        return 0
    return abs(hp.high - lp.low) / current_price


# ─── 主适配器 ──────────────────────────────────────────

class CZSCAdapter:

    def __init__(self, min_bars: int = 300, freq=Freq.F3):
        self.min_bars = min_bars
        self.freq = freq

    def analyze(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        df = dataframe.copy()

        for col in ["czsc_buy", "czsc_sell", "czsc_buy_type", "czsc_sell_type",
                     "czsc_buy_price", "czsc_sell_price",
                     "czsc_bi_count", "czsc_ubi_dir"]:
            df[col] = False if col in ("czsc_buy", "czsc_sell") else (
                "" if "type" in col or "dir" in col else None
            )
        df["czsc_bi_count"] = 0

        if len(df) < self.min_bars:
            return df

        all_bars = self._to_rawbars(df)
        if len(all_bars) < self.min_bars:
            return df

        init_bars = all_bars[:self.min_bars]
        try:
            c = CZSC(init_bars, max_bi_num=100)
        except Exception:
            return df

        n = len(df)
        for i in range(self.min_bars, n):
            bar = all_bars[i]
            try:
                c.update(bar)
            except Exception:
                continue

            bi_count = len(c.bi_list)
            df.iloc[i, df.columns.get_loc("czsc_bi_count")] = bi_count

            if bi_count < 5:
                continue

            self._eval_signals(c, df, i)

        return df

    def _eval_signals(self, c: CZSC, df: pd.DataFrame, idx: int):
        close = df.iloc[idx]["close"]

        # 一买 (底背驰) — 最高优先级
        buy1 = _check_first_buy(c)
        if buy1:
            if not self._dup_ok(df, idx, "buy1"):
                return
            df.iloc[idx, df.columns.get_loc("czsc_buy")] = True
            df.iloc[idx, df.columns.get_loc("czsc_buy_type")] = "buy1"
            df.iloc[idx, df.columns.get_loc("czsc_buy_price")] = close
            return

        # 一卖 (顶背驰)
        sell1 = _check_first_sell(c)
        if sell1:
            if not self._dup_ok(df, idx, "sell1"):
                return
            df.iloc[idx, df.columns.get_loc("czsc_sell")] = True
            df.iloc[idx, df.columns.get_loc("czsc_sell_type")] = "sell1"
            df.iloc[idx, df.columns.get_loc("czsc_sell_price")] = close
            return

        # 趋势跟随 (未完成笔买/卖点) — 比二买/二卖更可靠(在3m上)
        bs = _check_trend_bs(c, close)
        if bs == "buy":
            if not self._dup_ok(df, idx, "trend_buy"):
                return
            df.iloc[idx, df.columns.get_loc("czsc_buy")] = True
            df.iloc[idx, df.columns.get_loc("czsc_buy_type")] = "buy_trend"
            df.iloc[idx, df.columns.get_loc("czsc_buy_price")] = close
            return
        elif bs == "sell":
            if not self._dup_ok(df, idx, "trend_sell"):
                return
            df.iloc[idx, df.columns.get_loc("czsc_sell")] = True
            df.iloc[idx, df.columns.get_loc("czsc_sell_type")] = "sell_trend"
            df.iloc[idx, df.columns.get_loc("czsc_sell_price")] = close
            return

        # 二买 (中枢重叠确认) — 仅标记，不作为入场信号
        buy2 = _check_second_buy(c)
        if buy2:
            if not self._dup_ok(df, idx, "buy2"):
                return
            df.iloc[idx, df.columns.get_loc("czsc_buy")] = True
            df.iloc[idx, df.columns.get_loc("czsc_buy_type")] = "buy2"
            df.iloc[idx, df.columns.get_loc("czsc_buy_price")] = close
            # 不return — 让趋势信号也有机会同时触发

        # 二卖 (中枢重叠确认) — 仅标记，不作为入场信号
        sell2 = _check_second_sell(c)
        if sell2:
            if not self._dup_ok(df, idx, "sell2"):
                return
            df.iloc[idx, df.columns.get_loc("czsc_sell")] = True
            df.iloc[idx, df.columns.get_loc("czsc_sell_type")] = "sell2"
            df.iloc[idx, df.columns.get_loc("czsc_sell_price")] = close
            # 不return

    def _dup_ok(self, df: pd.DataFrame, idx: int, sig_type: str) -> bool:
        window = min(idx, 30)
        if window == 0:
            return True
        recent = df.iloc[idx - window:idx]
        if sig_type in ("buy1", "sell1", "buy2", "sell2"):
            return not ((recent['czsc_buy_type'] == sig_type).any() or
                        (recent['czsc_sell_type'] == sig_type).any())
        elif sig_type == "trend_buy":
            return not (recent['czsc_buy'] == True).any()
        elif sig_type == "trend_sell":
            return not (recent['czsc_sell'] == True).any()
        return True

    def _to_rawbars(self, df: pd.DataFrame) -> List[RawBar]:
        if not isinstance(df.index, pd.DatetimeIndex):
            if 'date' in df.columns:
                df = df.copy()
                df['dt'] = pd.to_datetime(df['date'])
            else:
                df = df.copy()
                df['dt'] = pd.to_datetime(df.index)

        if df['dt'].dt.tz is not None:
            df['dt'] = df['dt'].dt.tz_localize(None)

        mapped = df.rename(columns={'volume': 'vol'})

        if 'amount' not in mapped.columns:
            mapped['amount'] = mapped['vol'] * mapped['close']

        if 'symbol' not in mapped.columns:
            mapped['symbol'] = 'PAIR'

        needed = ['dt', 'symbol', 'open', 'close', 'high', 'low', 'vol', 'amount']
        bars_df = mapped[needed].copy()

        return format_standard_kline(bars_df, freq=self.freq)


# ─── 一买 / 一卖 (底背驰 / 顶背驰) ──────────────────────────

def _check_first_buy(c: CZSC) -> bool:
    if len(c.bi_list) < 5:
        return False

    for n in (21, 19, 17, 15, 13, 11, 9, 7, 5):
        bis = get_sub_elements(c.bi_list, di=1, n=n)
        if len(bis) != n:
            continue

        if not (len(bis) % 2 == 1
                and bis[-1].direction.value == "向下"
                and bis[0].direction == bis[-1].direction):
            continue

        if max(x.high for x in bis) != bis[0].high:
            continue
        if min(x.low for x in bis) != bis[-1].low:
            continue

        key_bis = []
        for i in range(0, len(bis) - 2, 2):
            if i == 0:
                key_bis.append(bis[i])
            else:
                b1, _, b3 = bis[i - 2:i + 1]
                if b3.low < b1.low:
                    key_bis.append(b3)

        pp_last = _bi_price_power(bis[-1])
        pp_prev = _bi_price_power(bis[-3])
        pp_mean = np.mean([_bi_price_power(x) for x in key_bis]) if key_bis else 0

        bc_price = pp_last < max(pp_prev, pp_mean)
        bc_volume = bis[-1].power_volume < max(
            bis[-3].power_volume, np.mean([x.power_volume for x in key_bis]))
        bc_length = bis[-1].length < max(
            bis[-3].length, np.mean([x.length for x in key_bis]))

        if bc_price and (bc_volume or bc_length):
            return True

    return False


def _check_first_sell(c: CZSC) -> bool:
    if len(c.bi_list) < 5:
        return False

    for n in (21, 19, 17, 15, 13, 11, 9, 7, 5):
        bis = get_sub_elements(c.bi_list, di=1, n=n)
        if len(bis) != n:
            continue

        if not (len(bis) % 2 == 1
                and bis[-1].direction.value == "向上"
                and bis[0].direction == bis[-1].direction):
            continue

        if max(x.high for x in bis) != bis[-1].high:
            continue
        if min(x.low for x in bis) != bis[0].low:
            continue

        key_bis = []
        for i in range(0, len(bis) - 2, 2):
            if i == 0:
                key_bis.append(bis[i])
            else:
                b1, _, b3 = bis[i - 2:i + 1]
                if b3.high > b1.high:
                    key_bis.append(b3)

        pp_last = _bi_price_power(bis[-1])
        pp_prev = _bi_price_power(bis[-3])
        pp_mean = np.mean([_bi_price_power(x) for x in key_bis]) if key_bis else 0

        bc_price = pp_last < max(pp_prev, pp_mean)
        bc_volume = bis[-1].power_volume < max(
            bis[-3].power_volume, np.mean([x.power_volume for x in key_bis]))
        bc_length = bis[-1].length < max(
            bis[-3].length, np.mean([x.length for x in key_bis]))

        if bc_price and (bc_volume or bc_length):
            return True

    return False


# ─── 二买 / 二卖 (中枢重叠确认) ────────────────────────────

def _check_second_buy(c: CZSC, w: int = 15, t: int = 4) -> bool:
    """二买 — 中枢视角下的并列二买 (cxt_second_bs_V240524)

    - 取最近 w 笔
    - 末笔方向向下，长度 >= 7
    - 末笔底分型区间与前面至少 t 个已结束笔的底分型区间有重叠
    - 未完成笔不超过 7 根K线
    """
    if len(c.bi_list) < w + 5:
        return False
    if len(c.bars_ubi) > 7:
        return False

    bis = get_sub_elements(c.bi_list, di=1, n=w)
    if len(bis) != w:
        return False

    last = bis[-1]
    if last.direction.value != "向下":
        return False
    if last.length < 7:
        return False

    last_fx_low = last.fx_b.low
    last_fx_high = last.fx_b.high
    fxs = [x.fx_b for x in bis[:-1] if x.length >= 7]

    zs_count = 0
    for fx in fxs:
        if max(fx.low, last_fx_low) < min(fx.high, last_fx_high):
            zs_count += 1

    return zs_count >= t


def _check_second_sell(c: CZSC, w: int = 15, t: int = 4) -> bool:
    """二卖 — 中枢视角下的并列二卖 (cxt_second_bs_V240524)"""
    if len(c.bi_list) < w + 5:
        return False
    if len(c.bars_ubi) > 7:
        return False

    bis = get_sub_elements(c.bi_list, di=1, n=w)
    if len(bis) != w:
        return False

    last = bis[-1]
    if last.direction.value != "向上":
        return False
    if last.length < 7:
        return False

    last_fx_low = last.fx_b.low
    last_fx_high = last.fx_b.high
    fxs = [x.fx_b for x in bis[:-1] if x.length >= 7]

    zs_count = 0
    for fx in fxs:
        if max(fx.low, last_fx_low) < min(fx.high, last_fx_high):
            zs_count += 1

    return zs_count >= t


# ─── 趋势跟随买/卖点 ──────────────────────────────────────

def _check_trend_bs(c: CZSC, current_price: float) -> Optional[str]:
    if len(c.bi_list) < 7:
        return None

    bis = get_sub_elements(c.bi_list, di=1, n=7)
    if len(bis) != 7:
        return None

    b1 = bis[-1]
    pp_seq = [_bi_price_power(x) for x in bis]
    pv_seq = [x.power_volume for x in bis]
    slope_seq = [abs(x.slope) for x in bis]

    snr = getattr(b1, 'SNR', 0) or 0
    if snr < 0.5:
        return None

    pp_ok = _bi_price_power(b1) >= np.max(pp_seq)
    pv_ok = b1.power_volume >= np.max(pv_seq)
    slope_ok = abs(b1.slope) >= np.max(slope_seq)
    if not (pp_ok or pv_ok or slope_ok):
        return None

    ubi = c.ubi
    if not ubi or len(ubi["raw_bars"]) < 3:
        return None

    ubi_ratio = _ubi_power_ratio(ubi, current_price)
    bi_power = _bi_price_power(b1)

    if bi_power <= 0 or ubi_ratio <= 0:
        return None

    if b1.direction.value == "向上":
        if 0.1 * bi_power < ubi_ratio < 0.7 * bi_power:
            return "buy"

    if b1.direction.value == "向下":
        if 0.2 * bi_power < ubi_ratio < 0.7 * bi_power:
            return "sell"

    return None
