from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame
from scipy.signal import argrelextrema

from Strategy003 import Strategy003

LEV_TIERS = {"chan_low": 10.0, "chan_mid": 25.0, "chan_high": 40.0, "chan_top": 55.0}
STAKE_TIERS = {"chan_low": 10.0, "chan_mid": 20.0, "chan_high": 35.0, "chan_top": 55.0}
MAX_CONSECUTIVE_LOSSES_HALT = 5
VOLATILITY_MAX_RATIO = 0.08


class Strategy003FuturesTop10(Strategy003):
    """缠论多TF(3m+15m) + 动态杠杆仓位 + 风控"""

    can_short = True
    timeframe = "3m"
    startup_candle_count = 500
    use_custom_stoploss = True

    minimal_roi = {"60": 0.02, "30": 0.04, "0": 0.06}
    trailing_stop = False

    min_stroke_bars = 5
    min_hub_strokes = 3
    conf_hub_tight_weight = 0.35
    conf_stroke_cnt_weight = 0.15
    conf_div_strength_weight = 0.50

    _consecutive_losses = 0

    def informative_pairs(self):
        return [(p, "15m") for p in self.dp.current_whitelist()]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)
        macd = ta.MACD(dataframe)
        dataframe['macd_hist'] = macd['macdhist']
        dataframe['ema200'] = ta.EMA(dataframe, timeperiod=200)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['vol_ma'] = dataframe['volume'].rolling(20).mean()

        # 15m HTF confirmation
        htf_bull = np.zeros(len(dataframe), dtype=bool)
        htf_bear = np.zeros(len(dataframe), dtype=bool)
        pair = metadata['pair']
        htf_df = self.dp.get_pair_dataframe(pair, "15m")
        if htf_df is not None and len(htf_df) > 100:
            htf_macd = ta.MACD(htf_df)
            bull_idx, bear_idx = self._simple_divergence(
                htf_df['high'].values, htf_df['low'].values,
                htf_macd['macdhist'].values, order=2)
            if len(htf_df) > 0:
                htf_tz = pd.to_datetime(htf_df['date'].values, utc=True)
                df_dates = pd.to_datetime(dataframe['date'].values, utc=True)
                for idx in bull_idx:
                    if idx < len(htf_tz):
                        htf_bull[df_dates >= htf_tz[idx]] = True
                for idx in bear_idx:
                    if idx < len(htf_tz):
                        htf_bear[df_dates >= htf_tz[idx]] = True
        dataframe['htf_bullish'] = htf_bull
        dataframe['htf_bearish'] = htf_bear

        self._detect_chan_structure(dataframe)
        return dataframe

    @staticmethod
    def _simple_divergence(high, low, macd_hist, order=2):
        bull_idx, bear_idx = set(), set()
        sl = argrelextrema(low, np.less, order=order)[0]
        for i in range(1, len(sl)):
            prev, curr = sl[i - 1], sl[i]
            if low[curr] < low[prev] and macd_hist[curr] > macd_hist[prev]:
                bull_idx.add(int(curr))
        sh = argrelextrema(high, np.greater, order=order)[0]
        for i in range(1, len(sh)):
            prev, curr = sh[i - 1], sh[i]
            if high[curr] > high[prev] and macd_hist[curr] < macd_hist[prev]:
                bear_idx.add(int(curr))
        return bull_idx, bear_idx

    # ==================================================================
    # Chan Theory Core (fast vectorized, 3m+15m dual timeframe)
    # ==================================================================
    @staticmethod
    def _find_top_fractals(high):
        n = len(high); mask = np.zeros(n, dtype=bool)
        for i in range(1, n - 1):
            if high[i] > high[i - 1] and high[i] > high[i + 1]: mask[i] = True
        return mask

    @staticmethod
    def _find_bottom_fractals(low):
        n = len(low); mask = np.zeros(n, dtype=bool)
        for i in range(1, n - 1):
            if low[i] < low[i - 1] and low[i] < low[i + 1]: mask[i] = True
        return mask

    @staticmethod
    def _build_strokes(top_idx, bottom_idx, high, low, min_bars):
        all_fx = [('top', idx, high[idx]) for idx in top_idx] + \
                 [('bottom', idx, low[idx]) for idx in bottom_idx]
        all_fx.sort(key=lambda x: x[1])
        strokes, pending = [], None
        for fx_type, idx, price in all_fx:
            if pending is None: pending = (fx_type, idx, price); continue
            pt, pi, pp = pending
            if fx_type != pt and (idx - pi) >= min_bars:
                strokes.append({
                    'start_idx': int(pi), 'end_idx': int(idx),
                    'start_price': float(pp), 'end_price': float(price),
                    'direction': 'up' if fx_type == 'top' else 'down',
                    'high': float(max(high[pi], high[idx])),
                    'low': float(min(low[pi], low[idx])),
                })
                pending = (fx_type, idx, price)
            elif (fx_type == 'top' and price > pp) or (fx_type == 'bottom' and price < pp):
                pending = (fx_type, idx, price)
        return strokes

    @staticmethod
    def _find_hubs(strokes, min_overlap=3):
        if len(strokes) < min_overlap: return []
        hubs, i = [], 0
        while i <= len(strokes) - min_overlap:
            w = strokes[i:i + min_overlap]
            oh, ol = min(s['high'] for s in w), max(s['low'] for s in w)
            if ol < oh:
                tightness = 1.0 - (oh - ol) / oh
                extra, lo, hi = 0, ol, oh
                for j in range(i + min_overlap, len(strokes)):
                    s = strokes[j]; nhi, nlo = min(hi, s['high']), max(lo, s['low'])
                    if nlo < nhi: extra += 1; hi, lo = nhi, nlo
                    else: break
                size = min_overlap + extra
                level = 1 + (1 if size >= 10 else 0) + (1 if size >= 28 else 0)
                hubs.append({
                    'start_idx': w[0]['start_idx'], 'end_idx': w[-1]['end_idx'],
                    'high': float(oh), 'low': float(ol),
                    'mid': float((oh + ol) / 2),
                    'tightness': tightness, 'extra_strokes': extra,
                    'total_overlaps': size,
                    'level': level, 'size': size,
                }); i += 1
            else: i += 1
        return hubs

    def _detect_chan_structure(self, dataframe):
        high_raw, low_raw = dataframe['high'].values, dataframe['low'].values
        n_raw = len(high_raw)

        # ---- chanlun.py inclusion removal (包含关系处理) ----
        high, low, idx_map = self._remove_inclusion(high_raw.copy(), low_raw.copy())

        n_clean = len(high)
        top_idx = np.where(self._find_top_fractals(high))[0]
        bot_idx = np.where(self._find_bottom_fractals(low))[0]
        strokes = self._build_strokes(top_idx, bot_idx, high, low, self.min_stroke_bars)
        hubs = self._find_hubs(strokes, self.min_hub_strokes)
        macd_hist = dataframe['macd_hist'].values

        first_buy = np.zeros(n_raw, dtype=bool)
        first_sell = np.zeros(n_raw, dtype=bool)
        buy_tag = np.full(n_raw, "", dtype=object)
        sell_tag = np.full(n_raw, "", dtype=object)

        if not hubs:
            dataframe['chan_first_buy'] = first_buy
            dataframe['chan_first_sell'] = first_sell
            dataframe['chan_tag'] = ""
            dataframe['chan_hub_count'] = 0
            return

        all_extras = [h['extra_strokes'] for h in hubs]
        max_extra = max(all_extras) if all_extras else 1

        for hub in hubs:
            hub_end, hub_high, hub_low = hub['end_idx'], hub['high'], hub['low']
            tight_score = hub['tightness']
            stroke_score = min(hub['extra_strokes'] / max(1, max_extra), 1.0)
            # chanlun.py level boost: higher level hubs = more significant breakout
            level = hub.get('level', 1)
            level_mult = 1.0 + (level - 1) * 0.3
            search_end = min(n_clean, hub_end + 50)

            for j in range(hub_end, search_end):
                if low[j] < hub_low:
                    bi = j + np.argmin(low[j:min(j + 20, n_clean)])
                    pz = slice(max(0, hub['start_idx'] - 30), hub['start_idx'])
                    if pz.stop > pz.start:
                        pi = pz.start + np.argmin(low[pz])
                        md = macd_hist[bi] - macd_hist[pi]
                        if low[bi] < low[pi] and md > 0 and bi < n_clean:
                            orig_i = idx_map[bi]
                            orig_pi = idx_map[pi]
                            if orig_i < n_raw:
                                first_buy[orig_i] = True
                                mr = np.abs(macd_hist[max(0, orig_i - 100):orig_i + 1]).max() or 1
                                ds = min(abs(md) / (mr + 1e-9), 1.0)
                                htf_ok = bool(dataframe['htf_bullish'].values[orig_i])
                                conf = (self.conf_hub_tight_weight * tight_score +
                                        self.conf_stroke_cnt_weight * stroke_score +
                                        self.conf_div_strength_weight * ds)
                                conf = conf * level_mult
                                if htf_ok: conf = min(conf * 1.4, 1.0)
                                buy_tag[orig_i] = self._conf_to_tag(conf)
                    break

            for j in range(hub_end, search_end):
                if high[j] > hub_high:
                    bi = j + np.argmax(high[j:min(j + 20, n_clean)])
                    pz = slice(max(0, hub['start_idx'] - 30), hub['start_idx'])
                    if pz.stop > pz.start:
                        pi = pz.start + np.argmax(high[pz])
                        md = macd_hist[pi] - macd_hist[bi]
                        if high[bi] > high[pi] and md > 0 and bi < n_clean:
                            orig_i = idx_map[bi]
                            if orig_i < n_raw:
                                first_sell[orig_i] = True
                                mr = np.abs(macd_hist[max(0, orig_i - 100):orig_i + 1]).max() or 1
                                ds = min(abs(md) / (mr + 1e-9), 1.0)
                                htf_ok = bool(dataframe['htf_bearish'].values[orig_i])
                                conf = (self.conf_hub_tight_weight * tight_score +
                                        self.conf_stroke_cnt_weight * stroke_score +
                                        self.conf_div_strength_weight * ds)
                                conf = conf * level_mult
                                if htf_ok: conf = min(conf * 1.4, 1.0)
                                sell_tag[orig_i] = self._conf_to_tag(conf)
                    break

        dataframe['chan_first_buy'] = first_buy
        dataframe['chan_first_sell'] = first_sell
        dataframe['chan_tag'] = [buy_tag[i] or sell_tag[i] for i in range(n_raw)]
        dataframe['chan_hub_count'] = len(hubs)

    @staticmethod
    def _remove_inclusion(high, low):
        """chanlun.py 包含关系处理: remove bars engulfed by neighbors.
        Returns (clean_high, clean_low, index_map) where index_map maps
        clean index → original index."""
        n = len(high)
        keep = np.ones(n, dtype=bool)
        i = 1
        while i < n - 1:
            if not keep[i]:
                i += 1; continue
            j = i + 1
            while j < n and not keep[j]:
                j += 1
            if j >= n: break
            if (low[i] <= low[j] and high[i] >= high[j]) or \
               (low[i] >= low[j] and high[i] <= high[j]):
                high[i] = max(high[i], high[j])
                low[i] = min(low[i], low[j])
                keep[j] = False
            else:
                i = j
        index_map = np.where(keep)[0]
        return high[keep], low[keep], index_map

    @staticmethod
    def _conf_to_tag(conf):
        if conf >= 0.7: return "chan_top"
        elif conf >= 0.5: return "chan_high"
        elif conf >= 0.3: return "chan_mid"
        return "chan_low"

    # ==================================================================
    def populate_entry_trend(self, dataframe, metadata):
        parent = super().populate_entry_trend(dataframe, metadata)
        atr, price = dataframe['atr'], dataframe['close']
        extreme_vol = (atr / price) > VOLATILITY_MAX_RATIO

        parent['enter_long'] = 0
        parent.loc[dataframe['chan_first_buy'], 'enter_long'] = 1
        parent.loc[dataframe['ema50'] <= dataframe['ema100'], 'enter_long'] = 0
        parent.loc[extreme_vol, 'enter_long'] = 0

        parent['enter_short'] = 0
        parent.loc[dataframe['chan_first_sell'], 'enter_short'] = 1
        parent.loc[dataframe['ema50'] >= dataframe['ema100'], 'enter_short'] = 0
        parent.loc[dataframe['ema100'] >= dataframe['ema200'], 'enter_short'] = 0
        parent.loc[extreme_vol, 'enter_short'] = 0

        parent['enter_tag'] = ''
        lm = parent['enter_long'] == 1
        sm = parent['enter_short'] == 1
        parent.loc[lm, 'enter_tag'] = dataframe.loc[lm, 'chan_tag']
        parent.loc[sm, 'enter_tag'] = dataframe.loc[sm, 'chan_tag']
        return parent

    def populate_exit_trend(self, dataframe, metadata):
        parent = super().populate_exit_trend(dataframe, metadata)
        parent.loc[dataframe['chan_first_sell'], 'exit_long'] = 1
        parent.loc[dataframe['chan_first_buy'], 'exit_short'] = 1
        return parent

    def custom_stoploss(self, pair, trade, current_time, current_rate,
                        current_profit, after_fill, **kwargs):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0: return None
        atr = dataframe.iloc[-1].get('atr', 0)
        if atr <= 0 or current_rate <= 0: return None
        leverage = getattr(trade, 'leverage', 25.0)
        hard_stop = max(-(max(2.0 * atr / current_rate, 0.005) * leverage), -0.25)
        if leverage <= 12:       trail_pct, act = 0.015, 0.02
        elif leverage <= 25:     trail_pct, act = 0.025, 0.03
        elif leverage <= 40:     trail_pct, act = 0.035, 0.04
        else:                    trail_pct, act = 0.05, 0.06
        if current_profit >= act:
            return max(current_profit - trail_pct, hard_stop)
        return hard_stop

    def confirm_trade_exit(self, pair, trade, order_type, amount, rate,
                           time_in_force, exit_reason, current_time, **kwargs):
        profit = trade.calc_profit_ratio(rate)
        self._consecutive_losses = self._consecutive_losses + 1 if profit <= 0 else 0
        return True

    def custom_stake_amount(self, pair, current_time, current_rate,
                            proposed_stake, min_stake, max_stake,
                            entry_tag, side, **kwargs):
        if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES_HALT: return 0
        base = STAKE_TIERS.get(entry_tag, proposed_stake) if entry_tag else proposed_stake
        return max(base, min_stake)

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side, **kwargs):
        return LEV_TIERS.get(entry_tag, 15.0) if entry_tag else 15.0
