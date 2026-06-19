"""
StrategyChanForce — Chan 结构定位 + K线力量确认 (v2 修复版)
Bug修复:
  1. zone容忍度收窄(0.3%), 只看最近2个中枢/5个笔极值
  2. 量缩检测用正确窗口(前20根 vs 近3根)
  3. 入场确认改为阳包阴(非简单突破前高)
  4. 出场用反向衰竭(独立逻辑, 不复用入场信号)
  5. 加趋势过滤: EMA20倾斜 + 最近N根涨跌比
"""
import sys
from pathlib import Path
import numpy as np
from freqtrade.strategy import IStrategy
from pandas import DataFrame

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "chan_lib"))
from Chan import CChan
from ChanConfig import CChanConfig
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE, DATA_FIELD
from Common.CTime import CTime
from KLine.KLine_Unit import CKLine_Unit


class StrategyChanForce(IStrategy):
    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "5m"
    stoploss = -0.99
    use_custom_stoploss = True
    trailing_stop = False
    minimal_roi = {"0": 1.0}
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 200

    # ============ 辅助函数 ============
    def _df_to_klus(self, dataframe):
        klus = []
        for idx, row in dataframe.iterrows():
            dt = row['date'].to_pydatetime()
            klus.append(CKLine_Unit({
                DATA_FIELD.FIELD_TIME: CTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, auto=False),
                DATA_FIELD.FIELD_OPEN: float(row['open']), DATA_FIELD.FIELD_HIGH: float(row['high']),
                DATA_FIELD.FIELD_LOW: float(row['low']), DATA_FIELD.FIELD_CLOSE: float(row['close']),
                DATA_FIELD.FIELD_VOLUME: float(row['volume']),
            }, autofix=True))
        return klus

    def _get_chan_zones(self, dataframe, pair):
        klus = self._df_to_klus(dataframe)
        if len(klus) < 100:
            return [], [], []
        config = CChanConfig({
            "bi_strict": False, "trigger_step": True,
            "gap_as_kl": True, "one_bi_zs": True,
            "zs_combine": True, "zs_combine_mode": "zs", "zs_algo": "normal",
            "divergence_rate": float("inf"), "min_zs_cnt": 0,
            "macd_algo": "peak", "bs_type": "1,1p,3a,3b",
            "print_warning": False, "kl_data_check": False,
            "max_kl_misalgin_cnt": 999999, "max_kl_inconsistent_cnt": 999999,
        })
        try:
            chan = CChan(code=pair, lv_list=[KL_TYPE.K_5M], config=config,
                         data_src=DATA_SRC.CCXT, autype=AUTYPE.QFQ)
            chan.trigger_load({KL_TYPE.K_5M: klus})
        except Exception:
            return [], [], []

        # 只看最近 2 个中枢
        zs_zones = []
        all_zs = []
        for seg in chan[0].seg_list[-3:]:
            for czs in seg.zs_lst:
                if not czs.is_one_bi_zs():
                    all_zs.append((czs.low, czs.high))
        zs_zones = all_zs[-2:]  # 最近 2 个

        # 只看最近 5 个笔极值
        bi_lows = [bi._low() for bi in chan[0].bi_list[-10:]][-5:]
        bi_highs = [bi._high() for bi in chan[0].bi_list[-10:]][-5:]

        return zs_zones, bi_lows, bi_highs

    # ============ 趋势检测 ============
    def _trend_ok(self, df, idx, is_long):
        """EMA20 趋势过滤: 只顺大势方向入场"""
        if idx < 50:
            return False
        closes = df['close'].iloc[max(0, idx - 30):idx + 1].values
        if len(closes) < 10:
            return False
        # 简单线性趋势
        x = np.arange(len(closes))
        slope = np.polyfit(x, closes, 1)[0]
        # EMA 方向
        ema20 = df['close'].iloc[max(0, idx - 30):idx + 1].ewm(span=20, adjust=False).mean().iloc[-1]
        ema5 = df['close'].iloc[max(0, idx - 30):idx + 1].ewm(span=5, adjust=False).mean().iloc[-1]

        if is_long:
            return slope > 0 and ema5 > ema20  # 上升趋势
        else:
            return slope < 0 and ema5 < ema20  # 下降趋势

    # ============ 力量衰竭 ============
    def _calc_force(self, df, idx, is_long):
        """判空方/多方衰竭。返回 True 表示衰竭确认"""
        if idx < 30:
            return False

        l = 20  # lookback
        start = max(0, idx - l)
        full_window = df.iloc[start:idx + 1]
        recent3 = full_window.tail(3)
        vol_recent = recent3['volume'].mean()
        vol_base = full_window.head(20)['volume'].mean()
        if vol_base == 0:
            return False

        score = 0

        # 1. K线实体萎缩
        if is_long:
            bearish = full_window[full_window['close'] < full_window['open']].tail(3)
            if len(bearish) >= 3:
                bodies = (bearish['open'] - bearish['close']).values
                if len(bodies) >= 3 and bodies[-1] < bodies[-2] < bodies[-3]:
                    score += 1
        else:
            bullish = full_window[full_window['close'] > full_window['open']].tail(3)
            if len(bullish) >= 3:
                bodies = (bullish['close'] - bullish['open']).values
                if len(bodies) >= 3 and bodies[-1] < bodies[-2] < bodies[-3]:
                    score += 1

        # 2. 量缩且价格不利
        price_chg = df['close'].iloc[idx] - df['close'].iloc[idx - 4]
        if is_long:
            if price_chg < 0 and vol_recent < vol_base * 0.7:
                score += 1
        else:
            if price_chg > 0 and vol_recent < vol_base * 0.7:
                score += 1

        # 3. 影线
        c, o, h, l = df['close'].iloc[idx], df['open'].iloc[idx], df['high'].iloc[idx], df['low'].iloc[idx]
        body = abs(c - o)
        if body > 0:
            if is_long:
                lower_wick = min(o, c) - l
                if lower_wick > body * 2.0:  # 收紧到 2.0x
                    score += 1
            else:
                upper_wick = h - max(o, c)
                if upper_wick > body * 2.0:
                    score += 1

        return score >= 2

    # ============ 入场区域 ============
    def _near_zone(self, price, zs_zones, bi_extremes, is_long):
        """价格是否在关注区域内"""
        TOL = 0.003  # 收紧到 0.3%
        if is_long:
            for zl, zh in zs_zones:
                if abs(price - zl) / zl < TOL:
                    return True
            for low in bi_extremes:
                if abs(price - low) / low < TOL:
                    return True
        else:
            for zl, zh in zs_zones:
                if abs(price - zh) / zh < TOL:
                    return True
            for high in bi_extremes:
                if abs(price - high) / high < TOL:
                    return True
        return False

    # ============ 出场力量 ============
    def _exit_force(self, df, idx, is_long):
        """出场: 多方/空方推不动了。返回 True 表示应出场"""
        if idx < 30:
            return False
        l = 20
        window = df.iloc[max(0, idx - l):idx + 1]
        recent3 = window.tail(3)

        if is_long:
            # 多方衰竭: 阳线变小 + 量缩
            bullish = window[window['close'] > window['open']].tail(3)
            if len(bullish) >= 3:
                bodies = (bullish['close'] - bullish['open']).values
                if len(bodies) >= 3 and bodies[-1] < bodies[-2] < bodies[-3]:
                    return True
        else:
            bearish = window[window['close'] < window['open']].tail(3)
            if len(bearish) >= 3:
                bodies = (bearish['open'] - bearish['close']).values
                if len(bodies) >= 3 and bodies[-1] < bodies[-2] < bodies[-3]:
                    return True
        return False

    # ============ Freqtrade 接口 ============
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata.get('pair', 'default')
        dataframe['cf_long'] = 0
        dataframe['cf_short'] = 0
        dataframe['cf_exit_long'] = 0
        dataframe['cf_exit_short'] = 0

        zs_zones, bi_lows, bi_highs = self._get_chan_zones(dataframe, pair)
        if not zs_zones and not bi_lows:
            return dataframe

        for i in range(50, len(dataframe)):
            price = dataframe['close'].iloc[i]
            oc = dataframe['open'].iloc[i]

            # Long: 顺趋势 + 在区域 + 力量衰竭 + 阳包阴
            if self._trend_ok(dataframe, i, is_long=True) and \
               self._near_zone(price, zs_zones, bi_lows, is_long=True) and \
               self._calc_force(dataframe, i, is_long=True):

                # 确认阳线吞没前一根阴线
                prev_o = dataframe['open'].iloc[i - 1]
                prev_c = dataframe['close'].iloc[i - 1]
                curr_o = dataframe['open'].iloc[i]
                curr_c = dataframe['close'].iloc[i]
                if curr_c > curr_o and curr_c > prev_c and curr_o < prev_c:
                    dataframe.at[dataframe.index[i], 'cf_long'] = 1

            # Short: 顺趋势 + 在区域 + 力量衰竭 + 阴包阳
            if self._trend_ok(dataframe, i, is_long=False) and \
               self._near_zone(price, zs_zones, bi_highs, is_long=False) and \
               self._calc_force(dataframe, i, is_long=False):

                prev_o = dataframe['open'].iloc[i - 1]
                prev_c = dataframe['close'].iloc[i - 1]
                curr_o = dataframe['open'].iloc[i]
                curr_c = dataframe['close'].iloc[i]
                if curr_c < curr_o and curr_c < prev_c and curr_o > prev_c:
                    dataframe.at[dataframe.index[i], 'cf_short'] = 1

            # 出场信号
            if self._exit_force(dataframe, i, is_long=True):
                dataframe.at[dataframe.index[i], 'cf_exit_long'] = 1
            if self._exit_force(dataframe, i, is_long=False):
                dataframe.at[dataframe.index[i], 'cf_exit_short'] = 1

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe['cf_long'] == 1, ['enter_long', 'enter_tag']] = (1, 'cf_long')
        dataframe.loc[dataframe['cf_short'] == 1, ['enter_short', 'enter_tag']] = (1, 'cf_short')
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe['cf_exit_long'] == 1, 'exit_long'] = 1
        dataframe.loc[dataframe['cf_exit_short'] == 1, 'exit_short'] = 1
        return dataframe

    def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, after_fill, **kwargs):
        if after_fill:
            return -0.08  # 初始 8% 保护
        if current_profit > 0.06:
            return -0.03
        if current_profit > 0.03:
            return -0.05
        return -0.08

    def custom_exit_price(self, pair, current_time, proposed_rate, entry_tag=None, side=None, **kwargs):
        return proposed_rate * 0.99985
    def custom_entry_price(self, pair, current_time, proposed_rate, entry_tag=None, side=None, **kwargs):
        return proposed_rate * 1.00015
