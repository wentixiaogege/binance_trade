from datetime import datetime, timedelta
from typing import Optional
import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame
from Strategy003 import Strategy003
from chanlun_adapter import ChanLunSignals

LEV_TIERS = {"chan_top": 100.0, "chan_high": 75.0}
STAKE_TIERS = {"chan_top": 80.0, "chan_high": 60.0}
MAX_CONSECUTIVE_LOSSES_HALT = 5
VOLATILITY_MAX_RATIO = 0.08


class StrategyChanlunFutures(Strategy003):
    """chanlun.py adapter signals + tight stop + big winners"""

    can_short = True
    timeframe = "3m"
    startup_candle_count = 500
    use_custom_stoploss = True
    minimal_roi = {"0": 0.30}
    trailing_stop = False
    _consecutive_losses = 0

    def populate_indicators(self, dataframe, metadata):
        dataframe = super().populate_indicators(dataframe, metadata)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        adapter = ChanLunSignals()
        dataframe = adapter.analyze(dataframe)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        parent = super().populate_entry_trend(dataframe, metadata)
        ev = (dataframe['atr'] / dataframe['close']) > VOLATILITY_MAX_RATIO
        parent['enter_long'] = 0; parent.loc[dataframe.get('chan_buy', False), 'enter_long'] = 1
        parent.loc[dataframe['ema50'] <= dataframe['ema100'], 'enter_long'] = 0; parent.loc[ev, 'enter_long'] = 0
        parent['enter_short'] = 0; parent.loc[dataframe.get('chan_sell', False), 'enter_short'] = 1
        parent.loc[dataframe['ema50'] >= dataframe['ema100'], 'enter_short'] = 0; parent.loc[ev, 'enter_short'] = 0
        # Level-based tag: L2+ pivots = higher confidence
        pivot_lv = dataframe.get('chan_pivot_level', 1)
        parent['enter_tag'] = 'chan_top'  # default
        has_entry = (parent['enter_long'] == 1) | (parent['enter_short'] == 1)
        parent.loc[(pivot_lv >= 2) & has_entry, 'enter_tag'] = 'chan_top'
        parent.loc[(pivot_lv < 2) & has_entry, 'enter_tag'] = 'chan_high'
        return parent

    def populate_exit_trend(self, dataframe, metadata):
        parent = super().populate_exit_trend(dataframe, metadata)
        parent.loc[dataframe.get('chan_sell', False), 'exit_long'] = 1
        parent.loc[dataframe.get('chan_buy', False), 'exit_short'] = 1
        return parent

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0: return None
        atr = dataframe.iloc[-1].get('atr', 0)
        if atr <= 0 or current_rate <= 0: return None
        lev = getattr(trade, 'leverage', 50.0)
        hs = max(-(max(1.0 * atr / current_rate, 0.003) * lev), -0.10)
        if lev <= 50:       tp, act = 0.02, 0.03
        elif lev <= 75:     tp, act = 0.03, 0.05
        else:               tp, act = 0.04, 0.06
        if current_profit >= act: return max(current_profit - tp, hs)
        return hs

    def confirm_trade_exit(self, pair, trade, order_type, amount, rate, time_in_force, exit_reason, current_time, **kwargs):
        p = trade.calc_profit_ratio(rate)
        self._consecutive_losses = self._consecutive_losses + 1 if p <= 0 else 0
        return True

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake, entry_tag, side, **kwargs):
        if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES_HALT: return 0
        base = STAKE_TIERS.get(entry_tag, proposed_stake) if entry_tag else proposed_stake
        return max(base, min_stake)

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return LEV_TIERS.get(entry_tag, 50.0) if entry_tag else 50.0
