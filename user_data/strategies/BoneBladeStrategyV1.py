# BoneBlade Strategy V1 — BB趋势 + ADX/DMI + RSI + 1h ATR止损
# v1.1: + dynamic leverage (1x-3x based on ADX)
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

class BoneBladeStrategyV1(IStrategy):

    WHITELIST = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
                 'DOGE/USDT', 'ADA/USDT', 'TRX/USDT', 'AVAX/USDT', 'LINK/USDT']

    buy_params = {
        "bb_period": 20, "bb_std": 2.0, "adx_threshold": 18,
        "atr_period": 14, "atr_sl_multiplier": 3.0, "informative_timeframe": "1h",
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

        bollinger = ta.BBANDS(dataframe, timeperiod=self.buy_params['bb_period'], nbdevup=self.buy_params['bb_std'], nbdevdn=self.buy_params['bb_std'])
        dataframe['bb_middle'] = bollinger['middleband']
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['plus_di'] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe['minus_di'] = ta.MINUS_DI(dataframe, timeperiod=14)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = [
            dataframe['close'] > dataframe['bb_middle'],
            dataframe['adx'] > self.buy_params['adx_threshold'],
            dataframe['plus_di'] > dataframe['minus_di'],
            dataframe['rsi'] > 35,
            dataframe['rsi'] < 70,
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
        rsi = last_candle.get('rsi', 50)
        close = last_candle.get('close', 0)
        bb_middle = last_candle.get('bb_middle', 0)

        # 基础杠杆 3x
        base_leverage = 3.0

        # 价格突破布林带中轨且趋势强劲时大幅提升杠杆
        if adx > 35 and 40 < rsi < 65 and close > bb_middle * 1.03:
            base_leverage = 20.0
        elif adx > 30 and 35 < rsi < 70 and close > bb_middle * 1.01:
            base_leverage = 15.0
        elif adx > 25 and rsi > 45:
            base_leverage = 10.0
        elif adx > 20:
            base_leverage = 8.0

        # 超买或价格跌破中轨时降低杠杆
        if rsi > 80 or adx < 15 or close < bb_middle:
            base_leverage = max(3.0, base_leverage * 0.6)

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
        rsi = last_candle.get('rsi', 50)
        if rsi < 30: risk_factor *= 1.2
        risk_factor = max(0.3, min(1.5, risk_factor))
        return max(min_stake, min(proposed_stake * risk_factor, max_stake))
