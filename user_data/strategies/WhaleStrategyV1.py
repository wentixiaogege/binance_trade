# Whale Strategy V1 — OBV资金流 + EMA趋势 + ADX/DMI + 1h ATR止损
# v1.1: + dynamic leverage (1x-3x based on ADX)
from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
import talib.abstract as ta
import freqtrade.vendor.qtpylib.indicators as qtpylib
import pandas as pd
pd.options.mode.chained_assignment = None
from functools import reduce
from datetime import datetime
import numpy as np
from freqtrade.strategy import merge_informative_pair
from freqtrade.persistence import Trade

class WhaleStrategyV1(IStrategy):

    WHITELIST = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
                 'DOGE/USDT', 'ADA/USDT', 'TRX/USDT', 'AVAX/USDT', 'LINK/USDT']

    buy_params = {
        "obv_ma_short": 5, "obv_ma_long": 20, "volume_spike_factor": 1.5,
        "adx_threshold": 18, "atr_period": 14, "atr_sl_multiplier": 3.0,
        "informative_timeframe": "1h",
    }

    minimal_roi = {"0": 1.0}
    stoploss = -0.80
    timeframe = '5m'
    startup_candle_count = 200
    process_only_new_candles = False
    trailing_stop = False
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    use_custom_stoploss = True

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(pair, self.buy_params['informative_timeframe']) for pair in pairs]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe=self.buy_params['informative_timeframe'])
        informative['atr_1h'] = ta.ATR(informative, timeperiod=self.buy_params['atr_period'])
        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, self.buy_params['informative_timeframe'], ffill=True)

        dataframe['obv'] = ta.OBV(dataframe['close'], dataframe['volume'])
        dataframe['obv_ma_short'] = ta.SMA(dataframe['obv'], timeperiod=self.buy_params['obv_ma_short'])
        dataframe['obv_ma_long'] = ta.SMA(dataframe['obv'], timeperiod=self.buy_params['obv_ma_long'])
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=20)
        dataframe['volume_spike'] = dataframe['volume'] > (dataframe['volume_ma'] * self.buy_params['volume_spike_factor'])
        dataframe['ema'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['plus_di'] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe['minus_di'] = ta.MINUS_DI(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 实盘优化：更严格的入场条件，减少亏损交易
        conditions = [
            qtpylib.crossed_above(dataframe['obv_ma_short'], dataframe['obv_ma_long']),
            dataframe['volume_spike'],
            dataframe['close'] > dataframe['ema'] * 1.02,  # 要求更强的趋势
            dataframe['adx'] > (self.buy_params['adx_threshold'] + 5),  # 提高ADX阈值
            dataframe['plus_di'] > dataframe['minus_di'] * 1.2,  # 要求更强的方向性
            dataframe['volume'] > dataframe['volume_ma'] * 1.8,  # 更高的成交量要求
        ]
        dataframe.loc[reduce(lambda x, y: x & y, conditions), 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return 3.0

        last_candle = dataframe.iloc[-1].squeeze()
        adx = last_candle.get('adx', 20)
        obv_ma_short = last_candle.get('obv_ma_short', 0)
        obv_ma_long = last_candle.get('obv_ma_long', 0)
        volume_spike = last_candle.get('volume_spike', False)
        volume = last_candle.get('volume', 0)
        volume_ma = last_candle.get('volume_ma', 1)

        # 基础杠杆 3x，根据市场强度动态调整 (3-100x)
        base_leverage = 3.0

        # 超级强势条件：高ADX + 量能激增 + OBV金叉
        if adx > 45 and volume > volume_ma * 3 and obv_ma_short > obv_ma_long * 1.05:
            base_leverage = 100.0
        # 强势条件：高ADX + 量能配合
        elif adx > 40 and volume > volume_ma * 2.5:
            base_leverage = 80.0
        # 趋势良好：ADX较高 + OBV趋势向上
        elif adx > 35 and obv_ma_short > obv_ma_long * 1.03 and volume > volume_ma * 2:
            base_leverage = 60.0
        # 标准趋势：ADX中等 + 量能确认
        elif adx > 30 and volume > volume_ma * 1.8:
            base_leverage = 40.0
        # 弱趋势：ADX一般 + 基础量能
        elif adx > 25:
            base_leverage = 20.0
        # 震荡市场：低ADX
        elif adx > 20:
            base_leverage = 10.0

        # 趋势反转或弱势时大幅降低杠杆
        if adx < 15 or obv_ma_short < obv_ma_long:
            base_leverage = max(3.0, base_leverage * 0.3)
        # 量能不足时降低杠杆
        elif volume < volume_ma * 0.8:
            base_leverage = max(3.0, base_leverage * 0.7)

        return min(base_leverage, max_leverage)

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return self.stoploss
        last_candle = dataframe.iloc[-1].squeeze()
        atr = last_candle.get('atr_1h', 0)
        if atr <= 0:
            return self.stoploss
        lev = trade.leverage or 1.0
        sl_mult = self.buy_params['atr_sl_multiplier']
        if current_profit > 0.10:
            trail_stop = (current_rate - atr * 1.0 - trade.open_rate) / trade.open_rate
            return max(trail_stop * lev, self.stoploss * lev)
        elif current_profit > 0.05:
            trail_stop = (current_rate - atr * 2.0 - trade.open_rate) / trade.open_rate
            return max(trail_stop * lev, self.stoploss * lev)
        elif current_profit > 0.02:
            return max(0.005 * lev, self.stoploss * lev)
        else:
            base_stop = (current_rate - atr * sl_mult - trade.open_rate) / trade.open_rate
            return max(base_stop * lev, self.stoploss * lev)

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                     current_rate: float, current_profit: float, **kwargs):
        """
        动态止盈机制 - 实盘优化关键
        """
        # 如果盈利超过50%，立即止盈50%仓位
        if current_profit > 0.50:
            return 'profit_take_50pct'

        # 如果盈利超过30%，止盈30%仓位
        elif current_profit > 0.30:
            return 'profit_take_30pct'

        # 如果盈利超过20%，止盈20%仓位
        elif current_profit > 0.20:
            return 'profit_take_20pct'

        # 如果盈利超过10%，止盈10%仓位
        elif current_profit > 0.10:
            return 'profit_take_10pct'

        return None

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            entry_tag: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake
        last_candle = dataframe.iloc[-1].squeeze()
        risk_factor = 1.0
        adx = last_candle.get('adx', 20)
        if adx > 30: risk_factor *= 1.3
        elif adx < 18: risk_factor *= 0.7
        risk_factor = max(0.3, min(1.5, risk_factor))
        return max(min_stake, min(proposed_stake * risk_factor, max_stake))
