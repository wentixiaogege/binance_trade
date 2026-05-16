"""
Chan Theory adapter for freqtrade.
Wraps chanlun.py pipeline: inclusion removal → fractals → strokes
→ segments → pivots → boundary break signals.
"""
import sys
import numpy as np
import pandas as pd

# Import chanlun.py classes (Seg, Pivot1, get_pivot, process_pivot)
sys.path.insert(0, '.')
import chanlun as _cl


class ChanLunSignals:
    """Run full chanlun.py pipeline and extract buy/sell signals."""

    def __init__(self, max_bars: int = 0):
        self.pivots = []
        self.tails = None
        self.df1 = None
        self.max_bars = max_bars  # 0 = use all data, N = use last N bars

    def analyze(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """Run pipeline, return dataframe with signal columns appended."""
        df = dataframe.copy()
        n = len(df)

        for col in ['chan_buy', 'chan_sell', 'chan_pivot_zg',
                     'chan_pivot_zd', 'chan_pivot_mid',
                     'chan_pivot_level', 'chan_pivot_trend']:
            df[col] = np.nan if 'pivot' in col else False

        if n < 200:
            return df

        # Rolling window: use only recent bars for pivot detection
        if self.max_bars > 0 and n > self.max_bars:
            work_df = df.iloc[-self.max_bars:].copy()
            # Need to reset datetime for the adapter's internal processing
        else:
            work_df = df

        # Step 1-2: Inclusion removal + df1
        self._build_df1(work_df)
        if self.df1 is None or len(self.df1) <= 60:
            return df

        # Step 3: judge() → od_list
        self._run_judge()

        # Step 4-6: segments → lines → pivots
        self._build_pivots()

        if not self.pivots:
            return df

        # Step 7: Map pivots to original dataframe (adjust timestamps for rolling window)
        df = self._map_pivots(df)

        # Step 8: Detect boundary break signals
        df = self._detect_signals(df)

        return df

    def _build_df1(self, df):
        """Inclusion removal → df1 (mirrors chanlun.py main())."""
        work = df[['low', 'high']].copy()
        work['datetime'] = df['date'].values

        # Skip leading equal/contained bars
        i = 0
        while i < len(work) - 1:
            if (work['low'].iloc[i] <= work['low'].iloc[i + 1] or
                work['high'].iloc[i] <= work['high'].iloc[i + 1]):
                i += 1
            else:
                break
        work = work.iloc[i:].reset_index(drop=True)

        # Inclusion removal
        while True:
            tlen = len(work)
            i = 0
            while i <= len(work) - 4:
                a, b = work.iloc[i + 2], work.iloc[i + 1]
                if ((a['low'] >= b['low'] and a['high'] <= b['high']) or
                    (a['low'] <= b['low'] and a['high'] >= b['high'])):
                    if b['low'] > work.iloc[i]['low']:
                        work.iloc[i + 2, 0] = max(work.iloc[i + 1:i + 3, 0])
                        work.iloc[i + 2, 1] = max(work.iloc[i + 1:i + 3, 1])
                    else:
                        work.iloc[i + 2, 0] = min(work.iloc[i + 1:i + 3, 0])
                        work.iloc[i + 2, 1] = min(work.iloc[i + 1:i + 3, 1])
                    work.drop(work.index[i + 1], inplace=True)
                    continue
                i += 1
            if len(work) == tlen:
                break
        work = work.reset_index(drop=True)

        # Fractal detection
        ul = [0]
        for i in range(len(work) - 2):
            if (work.iloc[i + 2, 0] < work.iloc[i + 1, 0] and
                work.iloc[i, 0] < work.iloc[i + 1, 0]):
                ul.append(1)
            elif (work.iloc[i + 2, 0] > work.iloc[i + 1, 0] and
                  work.iloc[i, 0] > work.iloc[i + 1, 0]):
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

        i = 0
        while (i < len(self.df1) - 2 and
               (sum(abs(self.df1.iloc[i + 1:i + 4, 2])) > 0 or
                self.df1.iloc[i, 2] == 0)):
            i += 1
        self.df1 = self.df1.iloc[i:].reset_index(drop=True)
        self.df1.rename(columns={0: 'od'}, inplace=True)

    def _run_judge(self):
        """Run chanlun.py judge() to build od_list."""
        # Store original globals
        orig_df1 = getattr(_cl, 'df1', None)
        orig_od = getattr(_cl, 'od_list', None)

        _cl.df1 = self.df1
        _cl.od_list = [0]

        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(10000)
        try:
            _cl.judge(0, 0, 1)
            self.od_list = list(_cl.od_list)
        finally:
            sys.setrecursionlimit(old_limit)
            _cl.df1 = orig_df1
            _cl.od_list = orig_od

    def _build_pivots(self):
        """Build segments → lines → pivots (mirrors chanlun.py main())."""
        # Set global df1 so Seg class can access it
        _cl.df1 = self.df1
        df1 = self.df1

        # Find start of first valid segment
        start = 0
        while start < len(self.od_list) - 5:
            sl = self.od_list[start:start + 4]
            d = -df1.iloc[sl[0], 2]
            if d == 1:
                valid = (df1.iloc[sl[1], 1] < df1.iloc[sl[3], 1] and
                         df1.iloc[sl[0], 0] < df1.iloc[sl[2], 0])
            else:
                valid = (df1.iloc[sl[1], 0] > df1.iloc[sl[3], 0] and
                         df1.iloc[sl[0], 1] > df1.iloc[sl[2], 1])
            if valid:
                break
            start += 1

        lines = []
        i = start
        ended = False
        seg = None
        while i <= len(self.od_list) - 4:
            seg = _cl.Seg(self.od_list[i:i + 4])
            label = False
            while not label and i <= len(self.od_list) - 6:
                i += 2
                label, _ = seg.grow(self.od_list[i + 2:i + 4])
                if seg.vertex[-1] > self.od_list[-3]:
                    ended = True
                    lines.append(seg.lines())
                    break
            if ended:
                break
            i = int(np.where(np.array(self.od_list) == seg.vertex[-1])[0][0])
            lines.append(seg.lines())

        if seg is None or len(lines) == 0:
            return

        # Tails
        low_list = df1.iloc[seg.vertex[-1]:, 0]
        high_list = df1.iloc[seg.vertex[-1]:, 1]
        lo = low_list.min()
        hi = high_list.max()

        if seg.finished:
            if lines[-1][0][1] < lines[-1][1][1]:
                lines.append([(seg.vertex[-1], lines[-1][1][1]),
                              (low_list.idxmin(), lo)])
            else:
                lines.append([(seg.vertex[-1], lines[-1][1][1]),
                              (high_list.idxmax(), hi)])
        else:
            if lines[-1][0][1] < lines[-1][1][1]:
                if lo > lines[-1][0][1]:
                    lines[-1] = [(lines[-1][0][0], lines[-1][0][1]),
                                 (high_list.idxmax(), hi)]
                elif low_list.idxmin() - seg.vertex[-1] >= 10:
                    lines.append([(seg.vertex[-1], lines[-1][1][1]),
                                  (low_list.idxmin(), lo)])
            else:
                if hi < lines[-1][0][1]:
                    lines[-1] = [(lines[-1][0][0], lines[-1][0][1]),
                                 (low_list.idxmin(), lo)]
                elif high_list.idxmax() - seg.vertex[-1] >= 10:
                    lines.append([(seg.vertex[-1], lines[-1][1][1]),
                                  (high_list.idxmax(), hi)])

        # Pivots
        a, self.tails = _cl.get_pivot(lines)
        self.pivots = _cl.process_pivot(a)

    def _map_pivots(self, df):
        """Map pivot boundaries to original dataframe by time."""
        if not self.pivots:
            return df

        last = self.pivots[-1]
        t_start = pd.Timestamp(last.start_time)
        df_dates = pd.to_datetime(df['date'].values, utc=True).tz_localize(None)
        mask = df_dates >= t_start
        if mask.any():
            idx = mask.argmax()
            df.loc[df.index[idx]:, 'chan_pivot_zd'] = last.zd
            df.loc[df.index[idx]:, 'chan_pivot_zg'] = last.zg
            df.loc[df.index[idx]:, 'chan_pivot_mid'] = last.mean

        return df

    def _detect_signals(self, df):
        """Last 2 pivots, boundary breaks with confirmed penetration (0.3%)."""
        if not self.pivots or len(self.pivots) < 2:
            return df

        df_dates = pd.to_datetime(df['date'].values, utc=True).tz_localize(None)
        triggered_buy = set(); triggered_sell = set()

        for p_idx, p in enumerate(self.pivots[-2:]):
            p_idx += max(0, len(self.pivots)-2)
            t_start = pd.Timestamp(p.start_time)
            after = df_dates >= t_start
            if not after.any(): continue
            si = after.argmax()

            for i in range(si, len(df)):
                low_i = df['low'].iloc[i]; high_i = df['high'].iloc[i]
                if p_idx not in triggered_buy and p.enter_d == -1 and low_i < p.zd * 0.997:
                    df.loc[df.index[i], 'chan_buy'] = True; triggered_buy.add(p_idx)
                if p_idx not in triggered_sell and p.enter_d == 1 and high_i > p.zg * 1.003:
                    df.loc[df.index[i], 'chan_sell'] = True; triggered_sell.add(p_idx)
                if p_idx in triggered_buy and p_idx in triggered_sell: break

        # Expose pivot trend and level
        if self.pivots:
            last = self.pivots[-1]
            t_start = pd.Timestamp(last.start_time)
            mask = df_dates >= t_start
            if mask.any():
                idx = mask.argmax()
                df.loc[df.index[idx]:, 'chan_pivot_level'] = getattr(last, 'level', 1)
                df.loc[df.index[idx]:, 'chan_pivot_trend'] = getattr(last, 'trend', 0)
        return df
