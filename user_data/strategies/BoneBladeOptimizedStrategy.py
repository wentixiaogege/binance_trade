# strategy_bone_blade_optimized.py
# BoneBlade Strategy v2 — 简化入场 + 1h ATR动态止损
# 核心：布林带超卖反弹 + 趋势确认 + ATR止损

from freqtrade.strategy.interface import IStrategy
from pandas import DataFrame
import talib.abstract as ta
import pandas as pd
pd.options.mode.chained_assignment = None
from functools import reduce
from datetime import datetime
import numpy as np
from freqtrade.strategy import merge_informative_pair

class BoneBladeOptimizedStrategy(IStrategy):
    """
    BoneBlade Strategy v2
    布林带超卖反弹 + EMA趋势 + ADX强度 + 1h ATR动态止损
    """

    WHITELIST = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
                 'DOGE/USDT', 'ADA/USDT', 'TRX/USDT', 'AVAX/USDT', 'LINK/USDT']

    buy_params = {
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_floor": 30,
        "rsi_ceiling": 50,
        "ema_period": 50,
        "adx_threshold": 18,
        "volume_factor": 1.2,
        "atr_period": 14,
        "atr_sl_multiplier": 3.0,
        "informative_timeframe": "1h",
    }

    sell_params = {}

    minimal_roi = {
        "0": 0.10,
        "60": 0.06,
        "120": 0.04,
        "240": 0.02,
        "480": 0
    }

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
        # 1h informative
        informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe=self.buy_params['informative_timeframe'])
        informative['atr_1h'] = ta.ATR(informative, timeperiod=self.buy_params['atr_period'])

        dataframe = merge_informative_pair(dataframe, informative, self.timeframe, self.buy_params['informative_timeframe'], ffill=True)

        # 5m indicators
        bollinger = ta.BBANDS(dataframe, timeperiod=self.buy_params['bb_period'],
                              nbdevup=self.buy_params['bb_std'], nbdevdn=self.buy_params['bb_std'])
        dataframe['bb_upper'] = bollinger['upperband']
        dataframe['bb_middle'] = bollinger['middleband']
        dataframe['bb_lower'] = bollinger['lowerband']
        dataframe['bb_position'] = (dataframe['close'] - dataframe['bb_lower']) / (dataframe['bb_upper'] - dataframe['bb_lower'] + 1e-10)

        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['ema'] = ta.EMA(dataframe, timeperiod=self.buy_params['ema_period'])
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['plus_di'] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe['minus_di'] = ta.MINUS_DI(dataframe, timeperiod=14)
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=20)
        dataframe['volume_spike'] = dataframe['volume'] > (dataframe['volume_ma'] * self.buy_params['volume_factor'])

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

    def custom_stoploss(self, pair: str, trade: 'Trade', current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """ATR动态止损 — 与Ghost v3相同的逻辑"""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return self.stoploss

        last_candle = dataframe.iloc[-1].squeeze()
        atr = last_candle.get('atr_1h', 0)
        if atr <= 0:
            atr = last_candle.get('atr', 0)
        if atr <= 0:
            return self.stoploss

        sl_mult = self.buy_params['atr_sl_multiplier']

        if current_profit > 0.10:
            trail_stop = (current_rate - atr * 1.0 - trade.open_rate) / trade.open_rate
            return max(trail_stop, self.stoploss)
        elif current_profit > 0.05:
            trail_stop = (current_rate - atr * 2.0 - trade.open_rate) / trade.open_rate
            return max(trail_stop, self.stoploss)
        elif current_profit > 0.02:
            return max(0.005, self.stoploss)
        else:
            base_stop = (current_rate - atr * sl_mult - trade.open_rate) / trade.open_rate
            return max(base_stop, self.stoploss)

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            entry_tag: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake

        last_candle = dataframe.iloc[-1].squeeze()
        risk_factor = 1.0

        adx = last_candle.get('adx', 20)
        if adx > 30:
            risk_factor *= 1.3
        elif adx < 18:
            risk_factor *= 0.7

        rsi = last_candle.get('rsi', 50)
        if rsi < 30:
            risk_factor *= 1.2

        risk_factor = max(0.3, min(1.5, risk_factor))
        adjusted_stake = proposed_stake * risk_factor
        return max(min_stake, min(adjusted_stake, max_stake))
