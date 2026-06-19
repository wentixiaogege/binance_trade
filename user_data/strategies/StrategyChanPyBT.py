"""StrategyChanPyBT — 回测专用, 从CSV读取预计算信号, 动态杠杆(置信度)"""
from pathlib import Path
import pandas as pd
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class StrategyChanPyBT(IStrategy):
    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "5m"

    # 动态杠杆: 置信度越高倍数越大
    LEV_MAP = {"mtf3": 15.0, "mtf2": 10.0, "mtf1": 5.0}

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, entry_tag, side) -> float:
        return self.LEV_MAP.get(entry_tag, 10.0)

    stoploss = -0.08
    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.06
    trailing_only_offset_is_reached = True

    minimal_roi = {"0": 0.15, "120": 0.08, "360": 0.04, "720": 0.02, "1440": 0}

    process_only_new_candles = False
    use_exit_signal = True
    startup_candle_count = 500

    _signals_df = None

    def _load_signals(self):
        if StrategyChanPyBT._signals_df is not None:
            return StrategyChanPyBT._signals_df

        csv_path = Path(__file__).resolve().parent.parent / "bsp_signals.csv"
        if not csv_path.exists():
            return None

        df = pd.read_csv(csv_path)
        df['date'] = pd.to_datetime(df['date'], utc=True)
        StrategyChanPyBT._signals_df = df
        return df

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['chan_buy'] = 0
        dataframe['chan_sell'] = 0
        dataframe['chan_tag'] = ''

        signals = self._load_signals()
        if signals is None or signals.empty:
            return dataframe

        pair = metadata.get('pair', '')
        pair_signals = signals[signals['pair'] == pair]
        if pair_signals.empty:
            return dataframe

        sig_map = {}
        for _, row in pair_signals.iterrows():
            sig_map[row['date']] = (
                int(row['chan_buy']),
                int(row['chan_sell']),
                int(row.get('bi_30m', 0)),
                int(row.get('bi_4h', 0)),
                int(row.get('bi_1d', 0)),
            )

        for i in dataframe.index:
            row_date = pd.Timestamp(dataframe.at[i, 'date'])
            if row_date.tzinfo is None:
                row_date = row_date.tz_localize('UTC')
            sig = sig_map.get(row_date)
            if sig:
                buy, sell, bi30, bi4h, bi1d = sig
                dataframe.at[i, 'chan_buy'] = buy
                dataframe.at[i, 'chan_sell'] = sell

                # 计算MTF支持度
                if buy:
                    support = (bi4h == 1) + (bi30 == 1) + (bi1d == 1)
                    dataframe.at[i, 'chan_tag'] = f"mtf{support}" if support >= 1 else "mtf0"
                elif sell:
                    support = (bi4h == -1) + (bi30 == -1) + (bi1d == -1)
                    dataframe.at[i, 'chan_tag'] = f"mtf{support}" if support >= 1 else "mtf0"

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        buy_mask = (dataframe['chan_buy'] == 1) & (dataframe['volume'] > 0)
        sell_mask = (dataframe['chan_sell'] == 1) & (dataframe['volume'] > 0)

        dataframe.loc[buy_mask, 'enter_long'] = 1
        dataframe.loc[sell_mask, 'enter_short'] = 1

        # entry_tag from precomputed confidence tier
        for i in dataframe[buy_mask].index:
            tag = dataframe.at[i, 'chan_tag']
            dataframe.at[i, 'enter_tag'] = tag if tag else 'mtf2'

        for i in dataframe[sell_mask].index:
            tag = dataframe.at[i, 'chan_tag']
            dataframe.at[i, 'enter_tag'] = tag if tag else 'mtf2'

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[(dataframe['chan_sell'] == 1) & (dataframe['volume'] > 0), 'exit_long'] = 1
        dataframe.loc[(dataframe['chan_buy'] == 1) & (dataframe['volume'] > 0), 'exit_short'] = 1
        return dataframe
