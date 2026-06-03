# Ghost Strategy V1 — EMA多周期趋势 + 4h过滤 + 布林带 + 动态止损
# v3.2: 层面三 — 15m时间框架替代5m，减少噪音
from functools import reduce
from datetime import datetime
import numpy as np
import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy.interface import IStrategy
from freqtrade.strategy import merge_informative_pair
from freqtrade.persistence import Trade


class GhostStrategyV1(IStrategy):

    can_short = True

    timeframe = '5m'
    informative_timeframe = '4h'
    startup_candle_count = 200
    process_only_new_candles = True

    # 风险参数
    minimal_roi = {"0": 0.50}
    stoploss = -0.50
    trailing_stop = False
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    use_custom_stoploss = True

    buy_params = {
        "adx_threshold": 22,
        "rsi_floor": 25,
        "rsi_ceiling": 80,
        "atr_period": 14,
        "atr_sl_multiplier": 8.0,
        "bb_period": 20,
        "bb_std": 2.0,
        "volume_spike_factor": 1.5,
        "regime_adx_min": 20,  # 4h ADX 最低阈值，低于此值视为震荡市不开仓
    }

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return [(pair, self.informative_timeframe) for pair in pairs]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 4h 趋势 + 市场状态
        informative = self.dp.get_pair_dataframe(
            pair=metadata['pair'], timeframe=self.informative_timeframe)
        informative['ema50'] = ta.EMA(informative, timeperiod=50)
        informative['adx'] = ta.ADX(informative, timeperiod=14)
        dataframe = merge_informative_pair(
            dataframe, informative, self.timeframe,
            self.informative_timeframe, ffill=True)
        if 'ema50_4h' not in dataframe.columns:
            dataframe['ema50_4h'] = informative['ema50']
        if 'adx_4h' not in dataframe.columns:
            dataframe['adx_4h'] = informative['adx']

        # EMA 双线
        dataframe['ema20'] = ta.EMA(dataframe, timeperiod=20)
        dataframe['ema50'] = ta.EMA(dataframe, timeperiod=50)

        # ADX / DMI
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['plus_di'] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe['minus_di'] = ta.MINUS_DI(dataframe, timeperiod=14)

        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # Bollinger Bands
        bb = ta.BBANDS(dataframe, timeperiod=self.buy_params['bb_period'],
                       nbdevup=self.buy_params['bb_std'],
                       nbdevdn=self.buy_params['bb_std'])
        dataframe['bb_upper'] = bb['upperband']
        dataframe['bb_middle'] = bb['middleband']
        dataframe['bb_lower'] = bb['lowerband']

        # ATR
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=self.buy_params['atr_period'])
        dataframe['atr_ratio'] = dataframe['atr'] / dataframe['close']

        # 成交量
        dataframe['volume_ma'] = ta.SMA(dataframe['volume'], timeperiod=20)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['enter_long'] = 0
        dataframe['enter_short'] = 0
        dataframe['enter_tag'] = ''

        # 市场状态过滤：4h ADX < 20 = 震荡市，不交易
        regime_ok = dataframe['adx_4h'] >= self.buy_params['regime_adx_min']

        # 4h 趋势（仅做空，只需下行趋势）
        ht_bearish = dataframe['close_4h'] < dataframe['ema50_4h']

        # EMA 排列
        ema_aligned_short = dataframe['ema20'] < dataframe['ema50']

        # DMI 方向
        di_ok_short = dataframe['minus_di'] > dataframe['plus_di']

        # 基础条件
        adx_ok = dataframe['adx'] > self.buy_params['adx_threshold']
        vol_ok = dataframe['volume'] > dataframe['volume_ma'] * self.buy_params['volume_spike_factor']
        rsi_ok_short = (dataframe['rsi'] < (100 - self.buy_params['rsi_floor'])) & (dataframe['rsi'] > (100 - self.buy_params['rsi_ceiling']))
        not_oversold = dataframe['close'] > dataframe['bb_lower'] * 1.01

        # === ENTER SHORT ONLY ===
        short_conditions = [
            regime_ok, ht_bearish, ema_aligned_short, di_ok_short,
            adx_ok, vol_ok, rsi_ok_short, not_oversold,
        ]
        dataframe.loc[reduce(lambda x, y: x & y, short_conditions), 'enter_short'] = 1
        dataframe.loc[reduce(lambda x, y: x & y, short_conditions), 'enter_tag'] = 'ghost_short_v3'

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        """ATR 动态止损 — 宽乘数补偿杠杆"""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return self.stoploss

        last_candle = dataframe.iloc[-1].squeeze()
        atr = last_candle.get('atr', 0)
        if atr <= 0:
            return self.stoploss

        if current_profit > 0.20:
            return -(atr * 0.5 / current_rate)
        elif current_profit > 0.10:
            return -(atr * 1.0 / current_rate)
        elif current_profit > 0.05:
            return -(atr * 2.0 / current_rate)
        else:
            return max(self.stoploss, -(atr * self.buy_params['atr_sl_multiplier'] / current_rate))

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        """杠杆 1x-3x"""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return 2.0

        last = dataframe.iloc[-1].squeeze()
        adx = last.get('adx', 20)
        atr_ratio = last.get('atr_ratio', 0.02)

        if atr_ratio > 0.05:
            return 1.0
        if atr_ratio > 0.03:
            return 2.0

        if adx > 40:
            return min(3.0, max_leverage)
        elif adx > 30:
            return min(2.0, max_leverage)
        return min(2.0, max_leverage)

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            entry_tag: str, **kwargs) -> float:
        """ATR 波动率自适应仓位"""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake

        last = dataframe.iloc[-1].squeeze()
        atr_ratio = last.get('atr_ratio', 0.02)

        if atr_ratio > 0.04:
            return max(min_stake, proposed_stake * 0.5)
        elif atr_ratio > 0.025:
            return max(min_stake, proposed_stake * 0.75)
        return proposed_stake

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        """动态止盈 + 时间止损"""
        if current_profit > 0.12:
            return 'profit_take_12pct'
        elif current_profit > 0.05:
            return 'profit_take_5pct'
        # 时间止损：持仓超8小时且盈利不足3%
        if trade.open_date_utc:
            hold_hours = (current_time.replace(tzinfo=None) - trade.open_date_utc.replace(tzinfo=None)).total_seconds() / 3600
            if hold_hours > 8 and current_profit < 0.03:
                return 'time_stop'
        return None
