# Athena Strategy V1 — EMA趋势 + MACD + ADX/DMI + HMA回调 + 1h ATR止损
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

class AthenaStrategyV1(IStrategy):

    WHITELIST = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
                 'DOGE/USDT', 'ADA/USDT', 'TRX/USDT', 'AVAX/USDT', 'LINK/USDT']

    buy_params = {
        "adx_threshold": 20, "atr_period": 14, "atr_sl_multiplier": 3.0,
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
        # 优化：更严格的入场条件，避免ETH等亏损交易
        conditions = [
            dataframe['ema_short'] > dataframe['ema_long'] * 1.01,  # 更强的EMA趋势
            dataframe['close'] > dataframe['ema_short'] * 1.02,  # 更强的价格确认
            dataframe['adx'] > (self.buy_params['adx_threshold'] + 5),  # 提高ADX阈值
            dataframe['plus_di'] > dataframe['minus_di'] * 1.2,  # 更强的方向性
            dataframe['rsi'] > 40,  # 提高RSI下限
            dataframe['rsi'] < 65,  # 降低RSI上限，避免超买
            dataframe['volume'] > dataframe['volume_ma'] * 1.5,  # 更高的成交量要求
        ]
        dataframe.loc[reduce(lambda x, y: x & y, conditions), 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                     current_rate: float, current_profit: float, **kwargs):
        """
        动态止盈机制 - 解决AthenaStrategyV1亏损的关键优化
        """
        # 如果盈利超过40%，立即止盈50%仓位
        if current_profit > 0.40:
            return 'profit_take_50pct'

        # 如果盈利超过25%，止盈30%仓位
        elif current_profit > 0.25:
            return 'profit_take_30pct'

        # 如果盈利超过15%，止盈20%仓位
        elif current_profit > 0.15:
            return 'profit_take_20pct'

        # 如果盈利超过8%，止盈10%仓位
        elif current_profit > 0.08:
            return 'profit_take_10pct'

        return None

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return 3.0

        last_candle = dataframe.iloc[-1].squeeze()
        adx = last_candle.get('adx', 20)
        rsi = last_candle.get('rsi', 50)
        ema_short = last_candle.get('ema_short', 0)
        ema_long = last_candle.get('ema_long', 0)
        volume = last_candle.get('volume', 0)
        volume_ma = last_candle.get('volume_ma', 1)

        # 基础杠杆 3x，最大限制在15x（从20x降至15x）
        base_leverage = 3.0

        # 趋势强劲且RSI健康时提升杠杆（从20x降至15x）
        if adx > 35 and 40 < rsi < 60 and volume > volume_ma * 1.2:
            base_leverage = 15.0  # 从20x降至15x
        elif adx > 30 and ema_short > ema_long * 1.02 and volume > volume_ma:
            base_leverage = 12.0  # 从15x降至12x
        elif adx > 25 and rsi > 45:
            base_leverage = 8.0   # 从10x降至8x
        elif adx > 20:
            base_leverage = 5.0   # 从8x降至5x

        # 超买或趋势弱势时大幅降低杠杆
        if rsi > 80 or adx < 15 or ema_short < ema_long:
            base_leverage = max(3.0, base_leverage * 0.5)

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
        multiplier = self.buy_params['atr_sl_multiplier']
        if current_profit > 0.10:
            trail_stop = (current_rate - atr * 1.5 - trade.open_rate) / trade.open_rate
            return max(trail_stop * lev, self.stoploss * lev)
        elif current_profit > 0.05:
            trail_stop = (current_rate - atr * 2.0 - trade.open_rate) / trade.open_rate
            return max(trail_stop * lev, self.stoploss * lev)
        elif current_profit > 0.02:
            return max(0.005 * lev, self.stoploss * lev)
        else:
            base_stop = (current_rate - atr * multiplier - trade.open_rate) / trade.open_rate
            return max(base_stop * lev, self.stoploss * lev)

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
