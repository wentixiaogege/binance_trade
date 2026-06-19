"""
FOttStrategy2 v13 — v10框架 + RSI动量过滤

v13: 在v10基础上加入RSI不对称过滤
     做多需要RSI>55(强势确认), 做空需要RSI<45(弱势确认)
     目的: 提高入场质量, 减少假突破导致的exit_signal亏损
"""

import logging
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from pandas import DataFrame
import talib.abstract as ta
import numpy as np
import freqtrade.vendor.qtpylib.indicators as qtpylib
from datetime import datetime

logger = logging.getLogger(__name__)


class FOttStrategy2(IStrategy):

    INTERFACE_VERSION: int = 3

    timeframe = '4h'
    startup_candle_count = 200

    minimal_roi = {"0": 0.08, "360": 0.04}
    stoploss = -0.10
    trailing_stop = False
    use_custom_stoploss = False

    can_short = True
    process_only_new_candles = True

    ott_percent = DecimalParameter(0.8, 2.5, default=1.4, space='buy')
    ott_pds = IntParameter(2, 5, default=2, space='buy')
    ott_cmo_period = IntParameter(5, 15, default=9, space='buy')

    # ========== OTT 指标 ==========
    def ott(self, dataframe: DataFrame, pds: int, percent: float, cmo_period: int):
        df = dataframe.copy()
        n = len(df)
        alpha = 2 / (pds + 1)

        df["ud1"] = np.where(
            df["close"] > df["close"].shift(1), (df["close"] - df["close"].shift()), 0
        )
        df["dd1"] = np.where(
            df["close"] < df["close"].shift(1), (df["close"].shift() - df["close"]), 0
        )
        df["UD"] = df["ud1"].rolling(cmo_period).sum()
        df["DD"] = df["dd1"].rolling(cmo_period).sum()
        df["CMO"] = ((df["UD"] - df["DD"]) / (df["UD"] + df["DD"])).fillna(0).abs()

        close_col = df.columns.get_loc("close")
        cmo_col = df.columns.get_loc("CMO")

        var_vals = np.zeros(n)
        for i in range(pds, n):
            cmo_i = df.iloc[i, cmo_col]
            close_i = df.iloc[i, close_col]
            prev_var = var_vals[i - 1]
            var_vals[i] = (alpha * cmo_i * close_i) + (1 - alpha * cmo_i) * prev_var
        df["Var"] = var_vals

        fark_pct = percent * 0.01
        df["fark"] = df["Var"] * fark_pct
        newlongstop = (df["Var"] - df["fark"]).values
        newshortstop = (df["Var"] + df["fark"]).values

        var_arr = df["Var"].values
        longstop = np.zeros(n)
        shortstop = np.full(n, 1e18)

        for i in range(1, n):
            if var_arr[i] > longstop[i - 1]:
                longstop[i] = max(newlongstop[i], longstop[i - 1])
            else:
                longstop[i] = newlongstop[i]

            if var_arr[i] < shortstop[i - 1]:
                shortstop[i] = min(newshortstop[i], shortstop[i - 1])
            else:
                shortstop[i] = newshortstop[i]

        direction = np.where(var_arr > longstop, 1, -1)
        mt = np.where(direction == 1, longstop, shortstop)
        ott_line = np.where(
            var_arr > mt,
            mt * (200 + percent) / 200,
            mt * (200 - percent) / 200,
        )
        ott_line = np.roll(ott_line, 2)
        ott_line[:2] = np.nan

        return DataFrame(index=df.index, data={
            "OTT": ott_line,
            "VAR": df["Var"],
        })

    # ========== 指标 ==========
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ott_data = self.ott(
            dataframe,
            pds=self.ott_pds.value,
            percent=self.ott_percent.value,
            cmo_period=self.ott_cmo_period.value,
        )
        dataframe['ott'] = ott_data['OTT']
        dataframe['var'] = ott_data['VAR']
        dataframe['ema50'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        return dataframe

    # ========== 入场 = OTT交叉 + EMA50趋势 + RSI动量过滤 ==========
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        long_cross = qtpylib.crossed_above(dataframe['var'], dataframe['ott'])
        short_cross = qtpylib.crossed_below(dataframe['var'], dataframe['ott'])
        bull = dataframe['close'] > dataframe['ema50']
        bear = ~bull

        # 做多: 趋势向上 + RSI>55 (强势)
        rsi_strong = dataframe['rsi'] > 55
        dataframe.loc[long_cross & bull & rsi_strong, 'enter_long'] = 1

        # 做空: 趋势向下 + RSI<45 (弱势)
        rsi_weak = dataframe['rsi'] < 45
        dataframe.loc[short_cross & bear & rsi_weak, 'enter_short'] = 1

        return dataframe

    # ========== 出场 = OTT反向交叉 ==========
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            qtpylib.crossed_below(dataframe['var'], dataframe['ott']),
            'exit_long',
        ] = 1
        dataframe.loc[
            qtpylib.crossed_above(dataframe['var'], dataframe['ott']),
            'exit_short',
        ] = 1
        return dataframe

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        return 3.0

    # ========== 清算保护 ==========
    def custom_exit(self, pair: str, trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        leverage = trade.leverage if hasattr(trade, 'leverage') and trade.leverage else 3.0
        liq_warning = -(1.0 / leverage) * 0.85
        if current_profit < liq_warning:
            return 'liquidation_risk'
        return None
