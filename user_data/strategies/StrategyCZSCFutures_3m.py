"""
StrategyCZSCFutures_3m — R:R止损止盈策略 v9 (15m)

入场：15m EMA趋势 + BB过滤器 + ADX/RSI/成交量确认
出场：固定 2:1 盈亏比 (14%止盈 / 7%止损)
"""

from datetime import datetime, timedelta
import talib.abstract as ta
from czsc_adapter import CZSCAdapter, Freq
from Strategy003 import Strategy003

LEV_TIERS = {"ema_long": 3.0, "ema_short": 3.0}
STAKE_TIERS = {"ema_long": 20.0, "ema_short": 20.0}
MAX_CONSECUTIVE_LOSSES_HALT = 5


class StrategyCZSCFutures_3m(Strategy003):
    """R:R策略 v9 — EMA趋势入场 + 固定盈亏比出场 (15m)"""

    can_short = True
    timeframe = "15m"
    startup_candle_count = 250
    use_custom_stoploss = True
    minimal_roi = {"0": 0.30}
    trailing_stop = False
    _consecutive_losses = 0

    def populate_indicators(self, dataframe, metadata):
        dataframe = super().populate_indicators(dataframe, metadata)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['ema9'] = ta.EMA(dataframe, timeperiod=9)
        dataframe['ema21'] = ta.EMA(dataframe, timeperiod=21)
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=20)

        bb = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_upper'] = bb['upperband']
        dataframe['bb_middle'] = bb['middleband']
        dataframe['bb_lower'] = bb['lowerband']

        adapter = CZSCAdapter(min_bars=150, freq=Freq.F15)
        dataframe = adapter.analyze(dataframe)
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        parent = super().populate_entry_trend(dataframe, metadata)

        ema9 = dataframe['ema9']
        ema21 = dataframe['ema21']
        close = dataframe['close']
        adx = dataframe['adx']
        rsi = dataframe['rsi']
        vol = dataframe['volume']
        vol_ma = dataframe['volume_ma']

        not_overbought = close < dataframe['bb_upper']
        not_oversold = close > dataframe['bb_lower']

        long_ok = (ema9 > ema21) & (adx > 25) & (rsi > 50) & (vol > vol_ma * 1.2) & not_overbought
        short_ok = (ema9 < ema21) & (adx > 25) & (rsi < 50) & (vol > vol_ma * 1.2) & not_oversold

        parent['enter_long'] = 0
        parent.loc[long_ok, 'enter_long'] = 1
        parent.loc[long_ok, 'enter_tag'] = 'ema_long'

        parent['enter_short'] = 0
        parent.loc[short_ok, 'enter_short'] = 1
        parent.loc[short_ok, 'enter_tag'] = 'ema_short'

        return parent

    def populate_exit_trend(self, dataframe, metadata):
        parent = super().populate_exit_trend(dataframe, metadata)
        # 不使用czsc出场信号 — 用custom_stoploss的固定R:R
        return parent

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or len(dataframe) == 0:
            return None
        atr = dataframe.iloc[-1].get('atr', 0)
        if atr <= 0 or current_rate <= 0:
            return None

        atr_pct = atr / current_rate
        # 硬止损: 5% (price) — 用3x杠杆 = 15% 最大亏损
        hs = -max(2.0 * atr_pct, 0.05)

        # 固定 2:1 盈亏比: 10%止盈 (price) → 30%利润 (3x)
        if current_profit >= 0.10:
            return max(current_profit - 0.02, hs)

        return hs

    def confirm_trade_exit(self, pair, trade, order_type, amount, rate, time_in_force, exit_reason, current_time, **kwargs):
        p = trade.calc_profit_ratio(rate)
        self._consecutive_losses = self._consecutive_losses + 1 if p <= 0 else 0
        return True

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake, entry_tag, side, **kwargs):
        if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES_HALT:
            return 0
        base = STAKE_TIERS.get(entry_tag, proposed_stake) if entry_tag else proposed_stake
        return max(base, min_stake)

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return LEV_TIERS.get(entry_tag, 50.0) if entry_tag else 50.0
