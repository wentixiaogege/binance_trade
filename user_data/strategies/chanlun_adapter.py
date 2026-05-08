"""
Chan Theory (缠论) adapter for freqtrade.
Wraps chanlun.py logic into a reusable, non-global class.
Input: freqtrade DataFrame (date, open, high, low, close, volume)
Output: buy/sell signal columns appended to DataFrame.
"""

import numpy as np
import pandas as pd
from typing import Tuple, List, Optional


class ChanLunAdapter:
    """
    Adapter that runs full Chan Theory analysis on a price DataFrame.

    Pipeline:
      raw OHLCV → inclusion removal → fractals → strokes (笔)
      → segments (线段) → pivots/中枢 → buy/sell points
    """

    def __init__(self, min_stroke_bars: int = 5, min_hub_overlap: int = 3):
        self.min_stroke_bars = min_stroke_bars
        self.min_hub_overlap = min_hub_overlap

        # Internal state (reset per run)
        self.df1 = None          # working dataframe after inclusion removal
        self.od_list = []        # confirmed fractal indices
        self.lines = []          # segments as [(start_idx, start_price), (end_idx, end_price)]
        self.pivots = []         # Pivot1 objects
        self.tails = None        # unfinished last segment info

    # ==================================================================
    # Public API
    # ==================================================================
    def analyze(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """
        Run full Chan Theory pipeline on a freqtrade DataFrame.
        Returns DataFrame with added columns:
          chan_buy1, chan_buy2, chan_buy3, chan_sell1, chan_sell2, chan_sell3
        """
        df = dataframe.copy()
        n = len(df)

        # Init output columns
        for col in ['chan_buy1', 'chan_buy2', 'chan_buy3',
                     'chan_sell1', 'chan_sell2', 'chan_sell3',
                     'chan_fractal_top', 'chan_fractal_bot',
                     'chan_hub_high', 'chan_hub_low', 'chan_hub_mid']:
            if col not in df.columns:
                df[col] = np.nan if 'price' in col.lower() or col.endswith(('high', 'low', 'mid')) else False

        if n < 60:
            return df

        # Step 1–2: inclusion removal + fractal detection
        self._build_df1(df)
        if self.df1 is None or len(self.df1) <= 60:
            return df

        # Step 3: Build strokes (od_list)
        self._build_strokes()

        # Step 4: Build segments (lines)
        self._build_segments()

        if len(self.lines) < 4:
            return df

        # Step 5: Build pivots/hubs
        self._build_pivots()

        if len(self.pivots) < 2:
            return df

        # Step 6: Detect buy/sell points
        self._detect_signals()

        # Step 7: Map signals back to original dataframe
        df = self._map_signals_to_df(df)

        # Step 8: Map hub boundaries
        df = self._map_hubs_to_df(df)

        return df

    # ==================================================================
    # Step 1–2: Inclusion removal + fractal detection → df1
    # ==================================================================
    def _build_df1(self, df: pd.DataFrame):
        """Process inclusion removal and build df1 with fractal markers."""
        work = df[['low', 'high']].copy()
        work['datetime'] = df['date'].values

        # Remove leading bars without proper high/low relationship
        i = 0
        while i < len(work) - 1:
            if work['low'].iloc[i] <= work['low'].iloc[i + 1] or \
               work['high'].iloc[i] <= work['high'].iloc[i + 1]:
                i += 1
            else:
                break
        work = work.iloc[i:].reset_index(drop=True)

        # Inclusion removal
        while True:
            temp_len = len(work)
            i = 0
            while i <= len(work) - 4:
                a = work.iloc[i + 2]
                b = work.iloc[i + 1]
                if (a['low'] >= b['low'] and a['high'] <= b['high']) or \
                   (a['low'] <= b['low'] and a['high'] >= b['high']):
                    if b['low'] > work.iloc[i]['low']:
                        work.iloc[i + 2, 0] = max(work.iloc[i + 1:i + 3, 0])
                        work.iloc[i + 2, 1] = max(work.iloc[i + 1:i + 3, 1])
                    else:
                        work.iloc[i + 2, 0] = min(work.iloc[i + 1:i + 3, 0])
                        work.iloc[i + 2, 1] = min(work.iloc[i + 1:i + 3, 1])
                    work.drop(work.index[i + 1], inplace=True)
                    continue
                i += 1
            if len(work) == temp_len:
                break
        work = work.reset_index(drop=True)

        # Fractal detection: 1=top fractal, -1=bottom fractal
        ul = [0]
        for i in range(len(work) - 2):
            if work.iloc[i + 2, 0] < work.iloc[i + 1, 0] and work.iloc[i, 0] < work.iloc[i + 1, 0]:
                ul.append(1)
            elif work.iloc[i + 2, 0] > work.iloc[i + 1, 0] and work.iloc[i, 0] > work.iloc[i + 1, 0]:
                ul.append(-1)
            else:
                ul.append(0)
        ul.append(0)

        self.df1 = pd.concat(
            (work[['low', 'high']], pd.DataFrame(ul), work['datetime']), axis=1
        )

        # Strip leading zeros
        i = 0
        while i < len(self.df1) - 2 and self.df1.iloc[i, 2] == 0:
            i += 1
        self.df1 = self.df1.iloc[i:].reset_index(drop=True)

        # Strip bars where next 3 don't contain any fractal
        i = 0
        while i < len(self.df1) - 2 and \
                (sum(abs(self.df1.iloc[i + 1:i + 4, 2])) > 0 or self.df1.iloc[i, 2] == 0):
            i += 1
        self.df1 = self.df1.iloc[i:].reset_index(drop=True)
        self.df1.rename(columns={0: 'od'}, inplace=True)

        if len(self.df1) <= 60:
            self.df1 = None

    # ==================================================================
    # Step 3: Build strokes (od_list)
    # ==================================================================
    def _build_strokes(self):
        """Run the recursive judge() to build the confirmed fractal list."""
        self.od_list = [0]
        self._judge(0, 0, 1)

    def _judge(self, prev_i, cur_i, d):
        """Recursive fractal confirmation (original chanlun judge())."""
        if cur_i + 4 >= len(self.df1) - 1:
            return

        if cur_i - prev_i < 4 or self.df1['od'].iloc[cur_i] != d:
            self._judge(prev_i, cur_i + 1, d)
            return

        # Check if a new extreme exists within 2-3 bars
        new_i, found = self._exist_new_extreme(cur_i, d, 2, 3)
        if found:
            self._judge(prev_i, new_i, d)
            return

        k = 4
        while True:
            if cur_i + k + 1 >= len(self.df1) - 1:
                return
            if self._exist_opposite(cur_i, d, k):
                break
            new_i, found = self._exist_new_extreme(cur_i, d, k, k)
            if found:
                self._judge(prev_i, new_i, d)
                return
            k += 1
            if cur_i + k >= len(self.df1) - 1:
                return

        prev_i = cur_i
        cur_i = cur_i + k
        self.od_list.append(prev_i)
        self._judge(prev_i, cur_i, -d)

    def _exist_opposite(self, cur_i, d, pos):
        if cur_i + pos >= len(self.df1):
            return False
        row_cur = self.df1.iloc[cur_i]
        row_opp = self.df1.iloc[cur_i + pos]
        if self.df1['od'].iloc[cur_i + pos] != -d:
            return False
        if d == 1:
            return row_cur['low'] > row_opp['low'] and row_cur['high'] > row_opp['high']
        else:
            return row_cur['low'] < row_opp['low'] and row_cur['high'] < row_opp['high']

    def _exist_new_extreme(self, cur_i, d, start, end):
        row_cur = self.df1.iloc[cur_i]
        for j in range(start, min(end + 1, len(self.df1) - cur_i)):
            row_j = self.df1.iloc[cur_i + j]
            if d == 1:
                if row_j['high'] >= row_cur['high']:
                    return cur_i + j, True
            else:
                if row_cur['low'] >= row_j['low']:
                    return cur_i + j, True
        return cur_i, False

    # ==================================================================
    # Step 4: Build segments (lines)
    # ==================================================================
    def _build_segments(self):
        """Build segments from confirmed fractals."""
        # Find start of first valid segment
        start = 0
        while start < len(self.od_list) - 5:
            if self._check_init_seg(self.od_list[start:start + 4]):
                break
            start += 1

        self.lines = []
        i = start
        ended = False
        while i <= len(self.od_list) - 4:
            seg = _Seg(self.df1, self.od_list[i:i + 4])
            label = False
            while not label and i <= len(self.od_list) - 6:
                i += 2
                label, _ = seg.grow(self.od_list[i + 2:i + 4])
                if seg.vertex[-1] > self.od_list[-3]:
                    ended = True
                    self.lines.append(seg.lines())
                    break
            if ended:
                break
            i = int(np.where(np.array(self.od_list) == seg.vertex[-1])[0][0])
            self.lines.append(seg.lines())

        # Handle tails (unfinished last segment)
        if len(self.lines) > 0:
            low_tail = self.df1.iloc[seg.vertex[-1]:, 0]
            high_tail = self.df1.iloc[seg.vertex[-1]:, 1]
            low_ext = low_tail.min()
            high_ext = high_tail.max()

            if seg.finished:
                if self.lines[-1][0][1] < self.lines[-1][1][1]:  # d==1 (rising)
                    self.lines.append([
                        (seg.vertex[-1], self.lines[-1][1][1]),
                        (low_tail.idxmin(), low_ext)
                    ])
                else:
                    self.lines.append([
                        (seg.vertex[-1], self.lines[-1][1][1]),
                        (high_tail.idxmax(), high_ext)
                    ])
            else:
                if self.lines[-1][0][1] < self.lines[-1][1][1]:  # d==1
                    if low_ext > self.lines[-1][0][1]:
                        self.lines[-1] = [
                            (self.lines[-1][0][0], self.lines[-1][0][1]),
                            (high_tail.idxmax(), high_ext)
                        ]
                    elif low_tail.idxmin() - seg.vertex[-1] >= 10:
                        self.lines.append([
                            (seg.vertex[-1], self.lines[-1][1][1]),
                            (low_tail.idxmin(), low_ext)
                        ])
                else:
                    if high_ext < self.lines[-1][0][1]:
                        self.lines[-1] = [
                            (self.lines[-1][0][0], self.lines[-1][0][1]),
                            (low_tail.idxmin(), low_ext)
                        ]
                    elif high_tail.idxmax() - seg.vertex[-1] >= 10:
                        self.lines.append([
                            (seg.vertex[-1], self.lines[-1][1][1]),
                            (high_tail.idxmax(), high_ext)
                        ])

    def _check_init_seg(self, start_l):
        d = -self.df1.iloc[start_l[0], 2]
        if d not in (1, -1):
            return False
        if d == 1:
            return (self.df1.iloc[start_l[1], 1] < self.df1.iloc[start_l[3], 1] and
                    self.df1.iloc[start_l[0], 0] < self.df1.iloc[start_l[2], 0])
        else:
            return (self.df1.iloc[start_l[1], 0] > self.df1.iloc[start_l[3], 0] and
                    self.df1.iloc[start_l[0], 1] > self.df1.iloc[start_l[2], 1])

    # ==================================================================
    # Step 5: Build pivots/hubs
    # ==================================================================
    def _build_pivots(self):
        pivots, tails = _get_pivot(self.df1, self.lines)
        self.pivots = _process_pivot(pivots)
        self.tails = tails

    # ==================================================================
    # Step 6: Detect buy/sell signals
    # ==================================================================
    def _detect_signals(self):
        """Detect buy/sell points: pivot boundary break + divergence at current price."""
        self.signals = {
            'buy1': None, 'buy2': None, 'buy3': None,
            'sell1': None, 'sell2': None, 'sell3': None,
        }

        if len(self.pivots) < 2 or self.df1 is None or len(self.df1) == 0:
            return

        cur_low = self.df1.iloc[-1, 0]
        cur_high = self.df1.iloc[-1, 1]
        cur_time = self.df1.iloc[-1, 3]

        # Use last 2 pivots for trend context
        last = self.pivots[-1]
        prev = self.pivots[-2]

        # ---- Buy signals ----
        # Buy: current low breaks below pivot dd (support) in a downtrend context
        if prev.trend == -1 and last.enter_d == -1:
            if cur_low < last.dd:
                self.signals['buy1'] = {'price': last.dd, 'time': cur_time}

        # Buy2: pullback to pivot zd (lower support) after downtrend
        if last.trend == -1 and last.gg > prev.gg:
            if cur_low < last.zd * 1.02:
                self.signals['buy2'] = {'price': last.zd, 'time': cur_time}

        # Buy3: breakout above pivot zg (upper resistance → support)
        if prev.trend == -1 and last.enter_d == -1:
            if cur_low < last.zd and prev.dd > last.dd:
                self.signals['buy3'] = {'price': last.zd, 'time': cur_time}

        # ---- Sell signals ----
        # Sell: current high breaks above pivot gg (resistance) in an uptrend context
        if prev.trend == 1 and last.enter_d == 1:
            if cur_high > last.gg:
                self.signals['sell1'] = {'price': last.gg, 'time': cur_time}

        # Sell2: rally to pivot zg (upper resistance) after uptrend
        if last.trend == 1 and last.dd < prev.dd:
            if cur_high > last.zg * 0.98:
                self.signals['sell2'] = {'price': last.zg, 'time': cur_time}

        # Sell3: breakdown below pivot zd (support → resistance)
        if prev.trend == 1 and last.enter_d == 1:
            if cur_high > last.zg and prev.gg < last.gg:
                self.signals['sell3'] = {'price': last.zg, 'time': cur_time}

    # ==================================================================
    # Step 7-8: Map results back to original DataFrame
    # ==================================================================
    def _map_signals_to_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """Map detected signals to the original dataframe by timestamp."""
        df_dates = pd.to_datetime(df['date'].values, utc=True).tz_localize(None)
        for key, sig in self.signals.items():
            col = f'chan_{key}'
            if col not in df.columns:
                df[col] = False
            if sig is not None:
                t = pd.Timestamp(sig['time'])
                mask = df_dates >= t
                if mask.any():
                    idx = mask.argmax()
                    df.loc[df.index[idx], col] = True
        return df

    def _map_hubs_to_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """Map the last pivot's hub boundaries to dataframe."""
        if len(self.pivots) > 0:
            last = self.pivots[-1]
            t = pd.Timestamp(last.start_time)
            df_dates = pd.to_datetime(df['date'].values, utc=True).tz_localize(None)
            mask = df_dates >= t
            if mask.any():
                idx = mask.argmax()
                df.loc[df.index[idx]:, 'chan_hub_high'] = last.zg
                df.loc[df.index[idx]:, 'chan_hub_low'] = last.zd
                df.loc[df.index[idx]:, 'chan_hub_mid'] = last.mean
        return df


# ======================================================================
# Segment class (adapted from original Seg)
# ======================================================================
class _Seg:
    def __init__(self, df1, start_l):
        self.df1 = df1
        self.start = start_l[0]
        if df1.iloc[start_l[0], 2] == 0:
            raise ValueError("Invalid seg init: fractal is 0")
        self.d = -df1.iloc[start_l[0], 2]
        self.finished = False
        self.vertex = list(start_l)
        self.gap = False
        if self.d == 1:
            self.cur_extreme = df1.iloc[start_l[3], 1]
            self.prev_extreme = df1.iloc[start_l[1], 1]
        else:
            self.cur_extreme = df1.iloc[start_l[3], 0]
            self.prev_extreme = df1.iloc[start_l[1], 0]

    def grow(self, new_l):
        if self.d == 1:  # rising seg
            if self.df1.iloc[new_l[1], 1] >= self.cur_extreme:
                self.gap = self.df1.iloc[new_l[0], 0] > self.prev_extreme
                self.prev_extreme = self.cur_extreme
                self.cur_extreme = self.df1.iloc[new_l[1], 1]
            else:
                v = self.vertex
                if (not self.gap and self.df1.iloc[new_l[1], 0] < self.df1.iloc[v[-1], 0]) or \
                   (self.gap and (self.df1.iloc[v[-1], 1] < self.df1.iloc[v[-3], 1]) and
                    (self.df1.iloc[v[-2], 0] < self.df1.iloc[v[-4], 0])):
                    self.finished = True
                    self.vertex = [i for i in self.vertex if i <= self.vertex[-1]]
                    return True, self.vertex[-1]
            self.vertex.extend(new_l)
            return False, 0
        else:  # falling seg
            if self.df1.iloc[new_l[1], 0] <= self.cur_extreme:
                self.gap = self.df1.iloc[new_l[0], 1] < self.prev_extreme
                self.prev_extreme = self.cur_extreme
                self.cur_extreme = self.df1.iloc[new_l[1], 0]
            else:
                v = self.vertex
                if (not self.gap and self.df1.iloc[new_l[1], 1] > self.df1.iloc[v[-1], 1]) or \
                   (self.gap and (self.df1.iloc[v[-1], 0] > self.df1.iloc[v[-3], 0]) and
                    (self.df1.iloc[v[-2], 1] > self.df1.iloc[v[-4], 1])):
                    self.finished = True
                    self.vertex = [i for i in self.vertex if i <= self.vertex[-1]]
                    return True, self.vertex[-1]
            self.vertex.extend(new_l)
            return False, 0

    def lines(self):
        if self.d == 1:
            return [(self.start, self.df1.iloc[self.start, 0]),
                    (self.vertex[-1], self.cur_extreme)]
        else:
            return [(self.start, self.df1.iloc[self.start, 1]),
                    (self.vertex[-1], self.cur_extreme)]


# ======================================================================
# Pivot class (adapted from original Pivot1)
# ======================================================================
class _Pivot:
    def __init__(self, df1, lines, d):
        self.df1 = df1
        self.trend = -2
        self.level = 1
        self.enter_d = d
        self.aft_l_price = 0
        self.future_zd = -float('inf')
        self.future_zg = float('inf')

        if d == 1:
            if lines[3][1][1] <= lines[1][0][1]:
                self.zg = min(lines[1][0][1], lines[3][0][1])
                self.zd = max(lines[3][1][1], lines[1][1][1])
                self.dd = lines[2][0][1]
                self.gg = max(lines[1][0][1], lines[2][1][1])
        else:
            if lines[3][1][1] >= lines[1][0][1]:
                self.zg = min(lines[1][1][1], lines[3][1][1])
                self.zd = max(lines[3][0][1], lines[1][0][1])
                self.dd = min(lines[2][1][1], lines[1][0][1])
                self.gg = lines[2][0][1]

        self.start_index = lines[1][0][0]
        self.end_index = lines[2][1][0]
        self.finished = 0
        self.size = 3
        self.mean = 0.5 * (self.zd + self.zg)
        self.start_time = df1.iloc[self.start_index, 3]
        self.leave_start_time = df1.iloc[self.end_index, 3]
        self.leave_end_time = df1.iloc[lines[3][1][0], 3]
        self.leave_d = -d
        self.leave_end_price = lines[3][1][1]
        self.leave_start_price = lines[3][0][1]
        self.enter_force = _seg_force(lines[0])
        self.leave_force = _seg_force(lines[3])
        self.prev2_force = _seg_force(lines[1])
        self.prev1_force = _seg_force(lines[2])
        self.prev2_end_price = lines[1][1][1]

    def grow(self, seg):
        self.prev2_force = self.prev1_force
        self.prev1_force = self.leave_force
        self.prev2_end_price = self.leave_start_price

        if seg[1][1] > seg[0][1]:  # rising line (d=1)
            if seg[1][1] >= self.zd and seg[0][1] <= self.zg and self.size <= 28:
                self.end_index = seg[0][0]
                self.size += 1
                self.dd = min(self.dd, seg[0][1])
                self.leave_force = _seg_force(seg)
                self.leave_start_time = self.df1.iloc[self.end_index, 3]
                self.leave_end_time = self.df1.iloc[seg[1][0], 3]
                self.leave_d = 1
                self.leave_start_price = seg[0][1]
                self.leave_end_price = seg[1][1]
                if self.size in [4, 7, 10, 19, 28]:
                    self.future_zd = max(self.future_zd, self.dd)
                    self.future_zg = min(self.future_zg, self.gg)
                if self.size in [10, 28]:
                    self.level += 1
                    self.zd = self.future_zd
                    self.zg = self.future_zg
                    self.future_zd = -float('inf')
                    self.future_zg = float('inf')
            else:
                if seg[1][1] >= self.zd and seg[0][1] <= self.zg:
                    self.dd = min(self.dd, seg[0][1])
                    self.finished = 0.5
                else:
                    self.finished = 1
                self.aft_l_price = seg[1][1]
        else:  # falling line (d=-1)
            if seg[1][1] <= self.zg and seg[0][1] >= self.zd and self.size <= 28:
                self.end_index = seg[0][0]
                self.size += 1
                self.gg = max(self.gg, seg[0][1])
                self.leave_force = _seg_force(seg)
                self.leave_start_time = self.df1.iloc[self.end_index, 3]
                self.leave_end_time = self.df1.iloc[seg[1][0], 3]
                self.leave_d = -1
                self.leave_start_price = seg[0][1]
                self.leave_end_price = seg[1][1]
                if self.size in [4, 7, 10, 19, 28]:
                    self.future_zd = max(self.future_zd, self.dd)
                    self.future_zg = min(self.future_zg, self.gg)
                if self.size in [10, 28]:
                    self.level += 1
                    self.zd = self.future_zd
                    self.zg = self.future_zg
                    self.future_zd = -float('inf')
                    self.future_zg = float('inf')
            else:
                if seg[1][1] <= self.zg and seg[0][1] >= self.zd:
                    self.gg = max(self.gg, seg[0][1])
                    self.finished = 0.5
                else:
                    self.finished = 1
                self.aft_l_price = seg[1][1]


# ======================================================================
# Pivot construction functions
# ======================================================================
def _seg_force(seg):
    """Calculate segment momentum/force."""
    bars = seg[1][0] - seg[0][0]
    if bars <= 0:
        return 0
    return 1000 * abs(seg[1][1] / seg[0][1] - 1) / bars


def _get_pivot(df1, lines):
    pivots = []
    i = 0
    while i < len(lines):
        d = 2 * int(lines[i][0][1] < lines[i][1][1]) - 1
        if i < len(lines) - 3:
            if d == 1:
                if lines[i + 3][1][1] <= lines[i + 1][0][1]:
                    pivot = _Pivot(df1, lines[i:i + 4], d)
                    i_j = 1
                    while i + i_j < len(lines) - 3 and pivot.finished == 0:
                        pivot.grow(lines[i + i_j + 3])
                        i_j += 1
                    i += pivot.size
                    pivots.append(pivot)
                else:
                    i += 1
            else:
                if lines[i + 3][1][1] >= lines[i + 1][0][1]:
                    pivot = _Pivot(df1, lines[i:i + 4], d)
                    i_j = 1
                    while i + i_j < len(lines) - 3 and pivot.finished == 0:
                        pivot.grow(lines[i + i_j + 3])
                        i_j += 1
                    i += pivot.size
                    pivots.append(pivot)
                else:
                    i += 1
        else:
            i += 1

    last = lines[-1]
    tails = [df1.iloc[last[0][0], 3], last[0][1],
             df1.iloc[last[1][0], 3], last[1][1],
             2 * int(last[1][1] > last[0][1]) - 1]
    return pivots, tails


def _process_pivot(pivots):
    for i in range(len(pivots) - 1):
        if pivots[i].level == 1 and pivots[i + 1].level == 1:
            if pivots[i].dd > pivots[i + 1].gg:
                pivots[i + 1].trend = -1
            elif pivots[i].gg < pivots[i + 1].dd:
                pivots[i + 1].trend = 1
            else:
                pivots[i + 1].trend = 0
        else:
            if pivots[i].gg > pivots[i + 1].gg and pivots[i].dd > pivots[i + 1].dd:
                pivots[i + 1].trend = -1
            elif pivots[i].gg < pivots[i + 1].gg and pivots[i].dd < pivots[i + 1].dd:
                pivots[i + 1].trend = 1
            else:
                pivots[i + 1].trend = 0
    return pivots


# ======================================================================
# Buy/Sell point detection functions
# ======================================================================
def _buy_point1(pro, tails, df1=None):
    if len(pro) <= 3 or tails[4] == 1 or pro[-1].size >= 8 or pro[-1].finished != 0:
        return False, 0
    if df1 is not None and (df1.iloc[-1, 0] / pro[-1].leave_end_price - 1 > 0 or
                             df1.iloc[-1, 0] > tails[3]):
        return False, 0
    if (pro[-1].prev2_end_price > pro[-1].leave_end_price and
        pro[-1].leave_start_time == tails[0] and
        (df1 is None or df1.iloc[-1, 0] < pro[-1].dd) and
        1.2 * pro[-1].leave_force < pro[-1].prev2_force and
        pro[-1].dd > pro[-1].leave_end_price):
        return True, pro[-1].dd
    return False, 0


def _buy_point2(pro, tails):
    if len(pro) <= 3 or tails[4] == 1 or pro[-1].size >= 8 or pro[-1].finished != 0:
        return False, 0
    if (pro[-1].prev2_end_price < pro[-1].leave_end_price and
        pro[-1].leave_start_time == tails[0] and
        pro[-1].prev2_end_price == pro[-1].dd and
        pro[-1].leave_start_price > 0.51 * (pro[-1].zd + pro[-1].zg)):
        return True, pro[-1].prev2_end_price
    return False, 0


def _buy_point3_des(pro, tails, df1=None):
    if len(pro) <= 2 or tails[4] == 1 or pro[-1].finished != 1 or pro[-1].level > 1:
        return False, 0
    if df1 is not None:
        if df1.iloc[-1, 0] / pro[-1].leave_end_price - 1 > 0 or df1.iloc[-1, 0] > tails[3]:
            return False, 0
        if (df1.iloc[-1, 0] < 0.98 * pro[-1].leave_end_price and
            df1.iloc[-1, 0] > 1.02 * pro[-1].zg and
            pro[-1].aft_l_price > 1.02 * pro[-1].zg and
            tails[0] == pro[-1].leave_end_time and
            pro[-1].leave_force > pro[-1].prev2_force and
            pro[-1].leave_end_price > pro[-1].prev2_end_price):
            return True, pro[-1].zg
    return False, 0


def _sell_point1(pro, tails, df1=None):
    if len(pro) <= 3 or tails[4] == -1 or pro[-1].size >= 8 or pro[-1].finished != 0:
        return False, 0
    if df1 is not None and (df1.iloc[-1, 1] / pro[-1].leave_end_price - 1 < 0 or
                             df1.iloc[-1, 0] < tails[3]):
        return False, 0
    if (pro[-1].prev2_end_price < pro[-1].leave_end_price and
        pro[-1].leave_start_time == tails[0] and
        (df1 is None or df1.iloc[-1, 0] > pro[-1].zg) and
        1.2 * pro[-1].leave_force < pro[-1].prev2_force):
        return True, pro[-1].zg
    return False, 0


def _sell_point2(pro, tails, df1=None):
    if len(pro) <= 3 or tails[4] == -1 or pro[-1].size >= 8 or pro[-1].finished != 0:
        return False, 0
    if df1 is not None and (df1.iloc[-1, 1] / pro[-1].leave_end_price - 1 < 0 or
                             df1.iloc[-1, 0] < tails[3]):
        return False, 0
    if (pro[-1].prev2_end_price > pro[-1].leave_end_price and
        pro[-1].leave_start_time == tails[0] and
        (df1 is None or df1.iloc[-1, 0] > 0.51 * (pro[-1].zd + pro[-1].zg)) and
        pro[-1].prev2_end_price == pro[-1].gg):
        return True, pro[-1].zg
    return False, 0


def _sell_point3_ris(pro, tails, df1=None):
    if len(pro) <= 3 or tails[4] == -1 or pro[-1].size >= 8 or pro[-1].finished != 1:
        return False, 0
    if df1 is not None and df1.iloc[-1, 0] < tails[3]:
        return False, 0
    if df1 is not None:
        if (1.02 * pro[-1].leave_end_price < df1.iloc[-1, 0] and
            pro[-1].leave_end_time == tails[0] and
            pro[-1].leave_force > pro[-1].prev2_force and
            df1.iloc[-1, 1] < pro[-1].zd):
            return True, pro[-1].zd
    return False, 0
