# TrendRider Strategy — 15m multi-TF trend + dynamic leverage (2x-5x) + trailing stop
# Entry: 4h trend EMA cross + 15m momentum + volume confirmation
# Exit: trailing stop only (100% win rate on trailing exits)
from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
import talib.abstract as ta
import pandas as pd
pd.options.mode.chained_assignment = None
from functools import reduce
from datetime import datetime
import numpy as np
from freqtrade.strategy import merge_informative_pair
from freqtrade.persistence import Trade

class TrendRiderStrategy(IStrategy):

    WHITELIST = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
                 'DOGE/USDT', 'ADA/USDT', 'TRX/USDT', 'AVAX/USDT', 'LINK/USDT']

    buy_params = {
        "ema_short": 20, "ema_long": 50, "adx_threshold": 20,
        "informative_timeframe": "4h",
    }

    # Wide stoploss. Leverage division auto-tightens:
    # At 5x: stoploss -0.25/5 = -5% price stop, trail +0.05/5 = 1% trail
    # At 3x: stoploss -0.25/3 = -8.3% price stop, trail +0.05/3 = 1.67% trail
    # At 2x: stoploss -0.25/2 = -12.5% price stop, trail +0.05/2 = 2.5% trail
    minimal_roi = {"0": 1.0}
    stoploss = -0.25
    trailing_stop = True
    trailing_stop_positive = 0.05
    trailing_stop_positive_offset = 0.10
    trailing_only_offset_is_reached = True

    timeframe = '15m'
    startup_candle_count = 200
    process_only_new_candles = False
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    use_custom_stoploss = False

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(pair, self.buy_params['informative_timeframe']) for pair in pairs]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 4h informative for trend direction + ADX strength
        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe=self.buy_params['informative_timeframe'])
        informative['ema_short'] = ta.EMA(informative, timeperiod=self.buy_params['ema_short'])
        informative['ema_long'] = ta.EMA(informative, timeperiod=self.buy_params['ema_long'])
        informative['adx'] = ta.ADX(informative, timeperiod=14)
        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, self.buy_params['informative_timeframe'], ffill=True)

        # 15m indicators
        dataframe['ema_short'] = ta.EMA(dataframe, timeperiod=self.buy_params['ema_short'])
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['plus_di'] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe['minus_di'] = ta.MINUS_DI(dataframe, timeperiod=14)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # Volume indicators
        dataframe['obv'] = ta.OBV(dataframe['close'], dataframe['volume'])
        dataframe['obv_ema'] = ta.EMA(dataframe['obv'], timeperiod=20)
        dataframe['volume_ratio'] = dataframe['volume'] / dataframe['volume'].rolling(20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        inf = self.buy_params['informative_timeframe']
        ema_short_4h = f"ema_short_{inf}"
        ema_long_4h = f"ema_long_{inf}"
        adx_4h = f"adx_{inf}"

        conditions = [
            # 4h trend: bullish EMA cross
            dataframe[ema_short_4h] > dataframe[ema_long_4h],
            # 4h trend strength: must be trending
            dataframe[adx_4h] > 18,
            # 15m: price above EMA20 (short-term momentum)
            dataframe['close'] > dataframe['ema_short'],
            # 15m: ADX trending (avoid chop)
            dataframe['adx'] > self.buy_params['adx_threshold'],
            # 15m: DMI bullish
            dataframe['plus_di'] > dataframe['minus_di'],
            # 15m: RSI healthy
            dataframe['rsi'] > 35,
            dataframe['rsi'] < 75,
            # Volume: OBV rising (accumulation)
            dataframe['obv'] > dataframe['obv_ema'],
            # Volume: above average activity
            dataframe['volume_ratio'] > 0.7,
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
            return 1.0
        inf = self.buy_params['informative_timeframe']
        adx_4h = f"adx_{inf}"
        last_candle = dataframe.iloc[-1].squeeze()
        adx = last_candle.get(adx_4h, 20)
        if adx > 35:
            return min(5.0, max_leverage)
        elif adx > 25:
            return min(3.0, max_leverage)
        return min(2.0, max_leverage)

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            entry_tag: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake
        inf = self.buy_params['informative_timeframe']
        adx_4h = f"adx_{inf}"
        last_candle = dataframe.iloc[-1].squeeze()
        risk_factor = 1.0
        adx = last_candle.get(adx_4h, 20)
        if adx > 30: risk_factor *= 1.5
        elif adx < 20: risk_factor *= 0.6
        rsi = last_candle.get('rsi', 50)
        if rsi < 30: risk_factor *= 1.3
        risk_factor = max(0.3, min(1.5, risk_factor))
        return max(min_stake, min(proposed_stake * risk_factor, max_stake))
