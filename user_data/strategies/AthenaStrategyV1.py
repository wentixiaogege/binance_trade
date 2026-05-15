# Athena Strategy V1 — EMA趋势 + MACD + ADX/DMI + HMA回调 + 1h ATR止损
from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
import talib.abstract as ta
import pandas as pd
pd.options.mode.chained_assignment = None
from functools import reduce
from datetime import datetime
import numpy as np
from freqtrade.strategy import merge_informative_pair

class AthenaStrategyV1(IStrategy):

    WHITELIST = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
                 'DOGE/USDT', 'ADA/USDT', 'TRX/USDT', 'AVAX/USDT', 'LINK/USDT']

    buy_params = {
        "adx_threshold": 20, "atr_period": 14, "atr_sl_multiplier": 3.0,
        "informative_timeframe": "1h",
    }

    minimal_roi = {"0": 0.10, "60": 0.06, "120": 0.04, "240": 0.02, "480": 0}
    stoploss = -0.08
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
        informative['atr_1h'] = ta.ATR(informative, timeperiod=14)
        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, self.buy_params['informative_timeframe'], ffill=True)

        dataframe['ema_short'] = ta.EMA(dataframe, timeperiod=20)
        dataframe['ema_long'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['plus_di'] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe['minus_di'] = ta.MINUS_DI(dataframe, timeperiod=14)
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=20)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            dataframe['ema_short'] > dataframe['ema_long'],
            dataframe['close'] > dataframe['ema_short'],
            dataframe['adx'] > self.buy_params['adx_threshold'],
            dataframe['plus_di'] > dataframe['minus_di'],
            dataframe['rsi'] > 35,
            dataframe['rsi'] < 70,
            dataframe['volume'] > dataframe['volume_ma'],
        ]
        dataframe.loc[reduce(lambda x, y: x & y, conditions), 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return self.stoploss
        last_candle = dataframe.iloc[-1].squeeze()
        atr = last_candle.get('atr_1h', 0)
        if atr <= 0:
            return self.stoploss
        multiplier = self.buy_params['atr_stop_multiplier']
        if current_profit > 0.10:
            trail_stop = (current_rate - atr * 1.5 - trade.open_rate) / trade.open_rate
            return max(trail_stop, self.stoploss)
        elif current_profit > 0.05:
            trail_stop = (current_rate - atr * 2.0 - trade.open_rate) / trade.open_rate
            return max(trail_stop, self.stoploss)
        elif current_profit > 0.02:
            return max(0.005, self.stoploss)
        else:
            base_stop = (current_rate - atr * multiplier - trade.open_rate) / trade.open_rate
            return max(base_stop, self.stoploss)

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            entry_tag: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake
        last_candle = dataframe.iloc[-1].squeeze()
        risk_factor = 1.0
        adx = last_candle.get('adx', 0)
        risk_factor *= min(1.3, adx / 25.0)
        volatility = last_candle.get('atr_1h', 0) / (current_rate + 1e-10)
        risk_factor *= max(0.5, 1.0 - volatility * 30)
        risk_factor = max(0.3, min(1.6, risk_factor))
        return max(min_stake, min(proposed_stake * risk_factor, max_stake))
