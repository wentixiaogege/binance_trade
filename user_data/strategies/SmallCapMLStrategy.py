"""
SmallCapMLStrategy — 小币种趋势猎手 ML增强版 (v1)

═══════════════════════════════════════════════════════════════
核心逻辑 (顺势回调耗尽入场):
═══════════════════════════════════════════════════════════════

做多:
  1) ML模型预测大趋势上涨 (未来1小时预期收益 > 0)
  2) 价格出现回调下跌 (连续阴线)
  3) 回调力量耗尽 → 出现阳线反转 + 放量确认 → 入场做多

做空:
  1) ML模型预测大趋势下跌 (未来1小时预期收益 < 0)
  2) 价格出现反弹上涨 (连续阳线)
  3) 反弹力量耗尽 → 出现阴线反转 + 放量确认 → 入场做空

关键点: 不是简单"回调就买"，而是等回调/反弹的力量消耗殆尽、
出现反转信号时才入场。这就是"顺势回调耗尽入场"。

═══════════════════════════════════════════════════════════════
ML增强 (替代 pytrendseries + OTT):
═══════════════════════════════════════════════════════════════
- LightGBM 回归模型预测未来20根K线(1小时)的收益率
- 多周期特征自动融合 (3m/15m/1h) + BTC相关性
- 模型每日自动重训练，持续学习市场变化
- 预测置信度驱动杠杆大小和仓位管理

═══════════════════════════════════════════════════════════════
保留 SmallCapHunterV1 的成熟机制:
═══════════════════════════════════════════════════════════════
- 差币禁闭: 3笔中2笔止损 → 禁闭24小时
- 动态杠杆: ML置信度 + EMA方向 + ATR波动率
- 动态止损: 杠杆越高止损越紧 (-20% ~ -35%)
- market止损: 小币种跳空时 limit 止损可能不成交
- ATR波动率仓位: 波动越大仓位越小
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame, Series

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter

logger = logging.getLogger(__name__)


class SmallCapMLStrategy(IStrategy):
    """
    ML增强版小币种策略:
    - FreqAI LightGBM 预测趋势方向
    - 回调入场 (顺势回调做多 / 反弹做空)
    - 双向交易 + 动态杠杆 + 禁闭机制
    """

    INTERFACE_VERSION: int = 3

    # === 基础配置 ===
    timeframe = '3m'
    can_short = True
    process_only_new_candles = True  # FreqAI 要求 True
    use_exit_signal = True
    startup_candle_count = 200

    # === 风险控制 ===
    stoploss = -0.30
    max_open_trades = 5

    minimal_roi = {"0": 0.12, "120": 0.08, "240": 0.05, "480": 0.03, "960": 0}

    trailing_stop = True
    trailing_stop_positive = 0.05
    trailing_stop_positive_offset = 0.08
    trailing_only_offset_is_reached = True
    use_custom_stoploss = True

    # 小币种 market 止损
    order_types = {
        'entry': 'limit',
        'exit': 'limit',
        'emergency_exit': 'market',
        'stoploss': 'market',
        'stoploss_on_exchange': False,
    }

    # === DCA 关闭 (3m周期马丁加仓不可行) ===
    position_adjustment_enable = False
    max_entry_position_adjustment = 0

    # === 保护机制 ===
    protections = [
        {"method": "CooldownPeriod", "stop_duration_candles": 4},
        {"method": "StoplossGuard", "lookback_period_candles": 24,
         "trade_limit": 4, "stop_duration_candles": 12},
        {"method": "MaxDrawdown", "lookback_period_candles": 48,
         "trade_limit": 20, "stop_duration_candles": 24,
         "max_allowed_drawdown": 0.20},
    ]

    # === FreqAI 配置 ===
    # 注: freqai 的主要配置在 config json 中，
    # 策略中只定义 feature engineering 方法
    # 如果 config 中没有 freqai 段，使用以下默认值
    freqai = {
        "enabled": True,
        "identifier": "smallcap_ml",
        "live_retrain_hours": 24,
        "train_period_days": 30,
        "backtest_period_days": 7,
        "fit_live_predictions_candles": 300,
        "purge_old_models": True,
        "feature_parameters": {
            "include_timeframes": ["3m", "15m", "1h"],
            "include_corr_pairlist": ["BTC/USDT:USDT"],
            "label_period_candles": 20,
            "include_shifted_candles": 2,
            "DI_threshold": 0.9,
            "weight_factor": 0.8,
            "principal_component_analysis": False,
            "use_SVM_to_remove_outliers": True,
            "indicator_max_period_candles": 100,
        },
        "data_split_parameters": {
            "test_size": 0.15,
            "random_state": 42,
            "shuffle": False,
        },
        "model_training_parameters": {
            "n_estimators": 300,
            "learning_rate": 0.03,
            "max_depth": 5,
            "num_leaves": 31,
            "verbosity": -1,
            "random_state": 42,
        },
    }

    # === 入场阈值 ===
    ml_long_threshold = DecimalParameter(0.002, 0.015, default=0.005, space='buy')
    ml_short_threshold = DecimalParameter(-0.015, -0.002, default=-0.005, space='buy')
    rsi_dip_max = IntParameter(25, 45, default=35, space='buy')
    rsi_bounce_min = IntParameter(55, 75, default=65, space='buy')

    # === 杠杆 ===
    base_leverage_val = IntParameter(3, 8, default=5, space='buy')
    max_leverage_val = IntParameter(10, 30, default=20, space='buy')

    # === 差币禁闭缓存 ===
    _pair_loss_ring: dict = {}
    _pair_jail_until: dict = {}

    # ========================================================================
    # FreqAI Feature Engineering
    # ========================================================================

    def feature_engineering_standard(self, dataframe: DataFrame,
                                      metadata: dict) -> DataFrame:
        """基础特征 (保留在原始周期)"""
        df = dataframe.copy()

        # 时间特征
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            df['%-hour'] = df['date'].dt.hour
            df['%-day_of_week'] = df['date'].dt.dayofweek

        # 价格特征
        df['%-raw_close'] = df['close']
        df['%-raw_open'] = df['open']
        df['%-raw_high'] = df['high']
        df['%-raw_low'] = df['low']
        df['%-raw_volume'] = df['volume']

        # 收益率
        df['%-returns_1'] = df['close'].pct_change(1)
        df['%-returns_3'] = df['close'].pct_change(3)
        df['%-returns_5'] = df['close'].pct_change(5)

        # 蜡烛形态
        df['%-body_size'] = abs(df['close'] - df['open'])
        df['%-body_ratio'] = df['%-body_size'] / (df['high'] - df['low'] + 1e-9)
        df['%-upper_shadow'] = df['high'] - df[['open', 'close']].max(axis=1)
        df['%-lower_shadow'] = df[['open', 'close']].min(axis=1) - df['low']
        df['%-hl_ratio'] = (df['high'] - df['close']) / (df['high'] - df['low'] + 1e-9)

        # 波动率
        df['%-volatility_20'] = df['close'].rolling(20).std() / df['close']

        # Volume
        df['%-volume_sma_20'] = ta.SMA(df['volume'], timeperiod=20)
        df['%-volume_ratio'] = df['volume'] / (df['%-volume_sma_20'] + 1e-9)

        # RSI
        df['%-rsi'] = ta.RSI(df, timeperiod=14)

        # MACD
        macd = ta.MACD(df)
        df['%-macd'] = macd['macd']
        df['%-macdsignal'] = macd['macdsignal']
        df['%-macdhist'] = macd['macdhist']

        # ATR
        df['%-atr'] = ta.ATR(df, timeperiod=14)
        df['%-atr_ratio'] = df['%-atr'] / df['close']

        # ADX
        df['%-adx'] = ta.ADX(df, timeperiod=14)

        # Bollinger Bands
        bb_upper, bb_mid, bb_lower = ta.BBANDS(df, timeperiod=20, nbdevup=2, nbdevdn=2)
        df['%-bb_width'] = (bb_upper - bb_lower) / (bb_mid + 1e-9)
        df['%-bb_position'] = (df['close'] - bb_lower) / (bb_upper - bb_lower + 1e-9)

        # EMA 趋势 (多周期)
        for period in [12, 26, 50]:
            df[f'%-ema_{period}'] = ta.EMA(df, timeperiod=period)
            df[f'%-close_ema_{period}'] = df['close'] / (df[f'%-ema_{period}'] + 1e-9)

        # EMA 多排/空排
        df['%-ema_bullish'] = (df['%-ema_12'] > df['%-ema_26']).astype(int)

        # CCI
        df['%-cci'] = ta.CCI(df, timeperiod=14)

        # MFI
        df['%-mfi'] = ta.MFI(df, timeperiod=14)

        # 连续涨跌K线数
        df['%-up_candle'] = (df['close'] > df['close'].shift(1)).astype(int)
        df['%-up_streak'] = (df['%-up_candle']
                             .groupby((df['%-up_candle'] == 0).cumsum())
                             .cumsum())
        df['%-dn_candle'] = (df['close'] < df['close'].shift(1)).astype(int)
        df['%-dn_streak'] = (df['%-dn_candle']
                             .groupby((df['%-dn_candle'] == 0).cumsum())
                             .cumsum())

        # 填充 NaN
        df.fillna(0, inplace=True)

        return df

    def feature_engineering_expand_all(self, dataframe: DataFrame,
                                        period: int,
                                        metadata: dict) -> DataFrame:
        """扩展特征 (在每个周期上自动计算)"""
        df = dataframe.copy()

        # 基础指标 (FreqAI 会自动在每个 include_timeframes 上调用此方法)
        df["%-rsi-period"] = ta.RSI(df, timeperiod=14)
        df["%-roc-period"] = ta.ROC(df, timeperiod=5)

        # Bollinger Bands
        bb_upper = ta.BBANDS(df, timeperiod=20, nbdevup=2, nbdevdn=2)[0]
        bb_lower = ta.BBANDS(df, timeperiod=20, nbdevup=2, nbdevdn=2)[2]
        bb_mid = ta.BBANDS(df, timeperiod=20, nbdevup=2, nbdevdn=2)[1]
        df["%-bb_width-period"] = (bb_upper - bb_lower) / (bb_mid + 1e-9)

        # EMA
        for p in [12, 26, 50]:
            df[f"%-ema_{p}-period"] = ta.EMA(df, timeperiod=p)
            df[f"%-close_ema_{p}-period"] = df['close'] / (df[f"%-ema_{p}-period"] + 1e-9)

        # ATR
        df["%-atr-period"] = ta.ATR(df, timeperiod=14)
        df["%-atr_ratio-period"] = df["%-atr-period"] / (df['close'] + 1e-9)

        # Volume
        df["%-volume_sma-period"] = ta.SMA(df['volume'], timeperiod=20)
        df["%-volume_ratio-period"] = df['volume'] / (df["%-volume_sma-period"] + 1e-9)

        # MACD
        macd = ta.MACD(df)
        df['%-macd-period'] = macd['macd']
        df['%-macdhist-period'] = macd['macdhist']

        # ADX
        df['%-adx-period'] = ta.ADX(df, timeperiod=14)

        # Price action
        df["%-pct_change-period"] = df['close'].pct_change()
        df["%-hl_ratio-period"] = (df['high'] - df['low']) / (df['close'] + 1e-9)
        df["%-body_ratio-period"] = abs(df['close'] - df['open']) / (df['high'] - df['low'] + 1e-9)

        # Lag features
        for lag in range(1, 4):
            df[f"%-close_lag_{lag}-period"] = df['close'].shift(lag)
            df[f"%-returns_lag_{lag}-period"] = df['close'].pct_change(lag)

        # 连续方向
        df['%-up-period'] = (df['close'] > df['close'].shift(1)).astype(int)
        df['%-up_streak-period'] = (df['%-up-period']
                                    .groupby((df['%-up-period'] == 0).cumsum())
                                    .cumsum())

        df.fillna(0, inplace=True)
        return df

    # ========================================================================
    # 入场指标 (非ML指标，用于回调检测)
    # ========================================================================

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # FreqAI 启动 (训练 + 预测)
        dataframe = self.freqai.start(dataframe, metadata, self)

        # === 回调检测指标 (不依赖ML，用于入场时机) ===
        dataframe['ema_12'] = ta.EMA(dataframe, timeperiod=12)
        dataframe['ema_26'] = ta.EMA(dataframe, timeperiod=26)
        dataframe['ema_50'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['atr_ratio'] = dataframe['atr'] / dataframe['close']
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['volume_sma_20'] = ta.SMA(dataframe['volume'], timeperiod=20)
        dataframe['volume_ratio'] = dataframe['volume'] / (dataframe['volume_sma_20'] + 1e-9)

        # 蜡烛方向
        dataframe['bearish_candle'] = (
            dataframe['close'] < dataframe['close'].shift(1)
        ).astype(int)
        dataframe['bearish_bars'] = (
            dataframe['bearish_candle']
            .groupby((dataframe['bearish_candle'] == 0).cumsum()).cumsum()
        )
        dataframe['bullish_candle'] = (
            dataframe['close'] > dataframe['close'].shift(1)
        ).astype(int)
        dataframe['bullish_bars'] = (
            dataframe['bullish_candle']
            .groupby((dataframe['bullish_candle'] == 0).cumsum()).cumsum()
        )

        # 新高/新低 (10 bar)
        dataframe['high_10'] = dataframe['close'].rolling(10).max().shift(1)
        dataframe['low_10'] = dataframe['close'].rolling(10).min().shift(1)
        dataframe['no_new_high'] = (dataframe['close'] <= dataframe['high_10']).astype(int)
        dataframe['no_new_low'] = (dataframe['close'] >= dataframe['low_10']).astype(int)

        # === 做多: 回调下跌耗尽检测 ===
        # 条件: EMA多头排列 + 连续阴线≥3根(卖压消耗) + 阳线反转 + 放量确认
        dataframe['exhaustion_buy_signal'] = (
            (dataframe['ema_12'] > dataframe['ema_26']) &      # 大趋势上涨
            (dataframe['bearish_bars'].shift(1) >= 3) &         # 回调下跌≥3根 (卖压耗尽)
            (dataframe['bullish_candle'] == 1) &                # 阳线反转 (耗尽确认)
            (dataframe['volume_ratio'] > 1.2)                   # 放量确认
        ).astype(int)

        # RSI 超卖反弹 (回调耗尽辅助信号)
        dataframe['rsi_oversold_bounce'] = (
            (dataframe['ema_12'] > dataframe['ema_26']) &
            (dataframe['rsi'].shift(1) < 35) &                  # 前一根RSI超卖
            (dataframe['rsi'] > dataframe['rsi'].shift(1)) &    # RSI回升 (耗尽)
            (dataframe['bullish_candle'] == 1) &
            (dataframe['volume_ratio'] > 1.0)
        ).astype(int)

        # === 做空: 反弹上涨耗尽检测 ===
        # 条件: EMA空头排列 + 连续阳线≥3根(买压消耗) + 阴线反转 + 放量确认
        dataframe['exhaustion_sell_signal'] = (
            (dataframe['ema_12'] < dataframe['ema_26']) &      # 大趋势下跌
            (dataframe['bullish_bars'].shift(1) >= 3) &         # 反弹上涨≥3根 (买压耗尽)
            (dataframe['bearish_candle'] == 1) &                # 阴线反转 (耗尽确认)
            (dataframe['volume_ratio'] > 1.2)                   # 放量确认
        ).astype(int)

        # RSI 超买回落 (反弹耗尽辅助信号)
        dataframe['rsi_overbought_fade'] = (
            (dataframe['ema_12'] < dataframe['ema_26']) &
            (dataframe['rsi'].shift(1) > 65) &                  # 前一根RSI超买
            (dataframe['rsi'] < dataframe['rsi'].shift(1)) &    # RSI回落 (耗尽)
            (dataframe['bearish_candle'] == 1) &
            (dataframe['volume_ratio'] > 1.0)
        ).astype(int)

        # === BTC 4h 方向 (做空过滤) ===
        try:
            inf_btc = self.dp.get_pair_dataframe(pair='BTC/USDT:USDT', timeframe='4h')
            if inf_btc is not None and len(inf_btc) >= 20:
                df_btc = inf_btc.copy()
                if 'date' in df_btc.columns:
                    df_btc['date'] = pd.to_datetime(df_btc['date'])
                    df_btc.set_index('date', inplace=True)
                elif not isinstance(df_btc.index, pd.DatetimeIndex):
                    df_btc.index = pd.to_datetime(df_btc.index)

                df_btc['ema_12'] = ta.EMA(df_btc, timeperiod=12)
                df_btc['ema_26'] = ta.EMA(df_btc, timeperiod=26)
                btc_bullish = (df_btc['ema_12'] > df_btc['ema_26']).astype(int)
                dataframe['btc_bullish'] = Series(
                    btc_bullish.values, index=df_btc.index
                ).reindex(dataframe.index, method='ffill').fillna(0).astype(int)
            else:
                dataframe['btc_bullish'] = 0
        except Exception:
            dataframe['btc_bullish'] = 0

        return dataframe

    # ========================================================================
    # 入场逻辑: ML大趋势确认 + 回调/反弹耗尽 = 入场
    # ========================================================================

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        pair = metadata['pair']

        # 差币禁闭检查
        if pair in self._pair_jail_until:
            if datetime.utcnow() < self._pair_jail_until[pair]:
                return dataframe
            else:
                del self._pair_jail_until[pair]

        # ML 预测
        pred_col = '&-s_target'  # FreqAI 回归器默认预测列名
        if pred_col not in dataframe.columns:
            logger.warning(f"[ML] {pair}: no prediction column '{pred_col}' in dataframe")
            return dataframe

        prediction = dataframe[pred_col]
        pred_mean = prediction.rolling(5).mean()

        # ═══════════════════════════════════════════════════════
        # 做多: ML看涨 + 回调下跌耗尽 → 入场做多
        # ═══════════════════════════════════════════════════════

        ml_long = pred_mean > self.ml_long_threshold.value
        adx_ok = dataframe['adx'] > 15

        # Type A: 回调耗尽入场
        # ML预测涨 + 连续阴线后阳线反转 + 放量 = 卖压耗尽，顺势做多
        long_exhaustion = (
            ml_long &
            (dataframe['exhaustion_buy_signal'] == 1) &
            adx_ok &
            (dataframe['no_new_low'] == 1)  # 不追创新低的币
        )
        dataframe.loc[long_exhaustion, 'enter_long'] = 1
        dataframe.loc[long_exhaustion, 'enter_tag'] = 'ml_exhaustion_long'

        # Type B: RSI超卖耗尽入场 (辅助)
        long_rsi_exhaustion = (
            ml_long &
            (dataframe['rsi_oversold_bounce'] == 1) &
            adx_ok &
            ~long_exhaustion  # 不与Type A重复
        )
        dataframe.loc[long_rsi_exhaustion, 'enter_long'] = 1
        dataframe.loc[long_rsi_exhaustion, 'enter_tag'] = 'ml_rsi_exhaustion_long'

        # 日志
        if long_exhaustion.iloc[-1] or long_rsi_exhaustion.iloc[-1]:
            i = dataframe.index[-1]
            logger.info(
                f"[ML-LONG] {pair} "
                f"pred={prediction.iloc[-1]:.4f} pred_mean={pred_mean.iloc[-1]:.4f} "
                f"rsi={dataframe.at[i,'rsi']:.0f} bearish_bars={dataframe.at[i,'bearish_bars']} "
                f"vol={dataframe.at[i,'volume_ratio']:.1f} adx={dataframe.at[i,'adx']:.0f}"
            )

        # ═══════════════════════════════════════════════════════
        # 做空: ML看跌 + 反弹上涨耗尽 → 入场做空
        # ═══════════════════════════════════════════════════════

        ml_short = pred_mean < self.ml_short_threshold.value

        # Type A: 反弹耗尽入场
        # ML预测跌 + 连续阳线后阴线反转 + 放量 = 买压耗尽，顺势做空
        short_exhaustion = (
            ml_short &
            (dataframe['exhaustion_sell_signal'] == 1) &
            adx_ok &
            (dataframe['no_new_high'] == 1) &    # 不空创新高的币
            (dataframe['btc_bullish'] == 0)       # BTC强势时不逆势做空
        )
        dataframe.loc[short_exhaustion, 'enter_short'] = 1
        dataframe.loc[short_exhaustion, 'enter_tag'] = 'ml_exhaustion_short'

        # Type B: RSI超买耗尽入场 (辅助)
        short_rsi_exhaustion = (
            ml_short &
            (dataframe['rsi_overbought_fade'] == 1) &
            adx_ok &
            (dataframe['btc_bullish'] == 0) &
            ~short_exhaustion
        )
        dataframe.loc[short_rsi_exhaustion, 'enter_short'] = 1
        dataframe.loc[short_rsi_exhaustion, 'enter_tag'] = 'ml_rsi_exhaustion_short'

        if short_exhaustion.iloc[-1] or short_rsi_exhaustion.iloc[-1]:
            i = dataframe.index[-1]
            logger.info(
                f"[ML-SHORT] {pair} "
                f"pred={prediction.iloc[-1]:.4f} pred_mean={pred_mean.iloc[-1]:.4f} "
                f"rsi={dataframe.at[i,'rsi']:.0f} bullish_bars={dataframe.at[i,'bullish_bars']} "
                f"vol={dataframe.at[i,'volume_ratio']:.1f} adx={dataframe.at[i,'adx']:.0f}"
            )

        return dataframe

    # ========================================================================
    # 出场逻辑
    # ========================================================================

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # ML预测反转 → 出场
        pred_col = '&-s_target'
        if pred_col not in dataframe.columns:
            return dataframe

        prediction = dataframe[pred_col]
        pred_mean = prediction.rolling(5).mean()

        # 做多出场: ML预测转负
        long_exit = (
            pred_mean < -0.002
        )
        dataframe.loc[long_exit, 'exit_long'] = 1

        # 做空出场: ML预测转正
        short_exit = (
            pred_mean > 0.002
        )
        dataframe.loc[short_exit, 'exit_short'] = 1

        return dataframe

    # ========================================================================
    # 自定义出场 (增强版，保留原策略的 exhaustion 检测)
    # ========================================================================

    def custom_exit(self, pair: str, trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        # 清算保护
        if current_profit < -0.50:
            return 'liquidation_risk'

        # 亏损交给 stoploss / trailing stop
        if current_profit <= 0:
            return None

        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) < 2:
            return None

        last = dataframe.iloc[-1].squeeze()
        prev = dataframe.iloc[-2].squeeze()

        if trade.is_short:
            # 做空: 跌到尽头 (连续阳线反弹)
            if last.get('bullish_bars', 0) >= 3 and last.get('rsi', 50) > 55:
                return 'exhaustion_short'
        else:
            # 做多: 涨到尽头 (连续阴线回调)
            if last.get('bearish_bars', 0) >= 3 and last.get('rsi', 50) < 45:
                return 'exhaustion_long'

        # EMA 趋势反转
        if trade.is_short:
            ema_bull = (prev.get('ema_12', 0) <= prev.get('ema_26', 0) and
                        last.get('ema_12', 0) > last.get('ema_26', 0))
            if ema_bull:
                return 'ema_flip_short'
        else:
            ema_bear = (prev.get('ema_12', 0) >= prev.get('ema_26', 0) and
                        last.get('ema_12', 0) < last.get('ema_26', 0))
            if ema_bear:
                return 'ema_flip_long'

        return None

    # ========================================================================
    # 动态杠杆: 基于 ML 置信度
    # ========================================================================

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return 2.0

        last = dataframe.iloc[-1].squeeze()
        atr_ratio = last.get('atr_ratio', 0.03)

        # 高波动降杠杆
        if atr_ratio > 0.08:
            return 2.0

        # ML 置信度
        pred_col = '&-s_target'
        confidence = abs(last.get(pred_col, 0))
        pred_mean = abs(dataframe[pred_col].rolling(5).mean().iloc[-1])

        # EMA多排/空排
        ema_bullish = last.get('ema_12', 0) > last.get('ema_26', 0)

        # 置信度等级
        if pred_mean > 0.008 and atr_ratio < 0.04:
            lev = float(self.max_leverage_val.value)
        elif pred_mean > 0.004 and atr_ratio < 0.06:
            lev = float(self.max_leverage_val.value * 0.7)
        elif pred_mean > 0.001:
            lev = float(self.base_leverage_val.value)
        else:
            lev = float(self.base_leverage_val.value * 0.6)

        # EMA 方向不对降杠杆
        if side == 'long' and not ema_bullish:
            lev *= 0.5
        elif side == 'short' and ema_bullish:
            lev *= 0.5

        return max(1.0, min(lev, float(self.max_leverage_val.value)))

    # ========================================================================
    # 动态止损: 杠杆越高止损越紧
    # ========================================================================

    def custom_stoploss(self, pair: str, trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        leverage = trade.leverage if hasattr(trade, 'leverage') else 5.0
        if leverage >= 25:
            return -0.35
        elif leverage >= 15:
            return -0.30
        elif leverage >= 8:
            return -0.25
        else:
            return -0.20

    # ========================================================================
    # 仓位: ATR波动率 + BTC方向调整
    # ========================================================================

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float, max_stake: float,
                            entry_tag: str, side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake

        last = dataframe.iloc[-1].squeeze()
        atr_ratio = last.get('atr_ratio', 0.03)
        btc_bull = last.get('btc_bullish', 1)

        # 波动率越高仓位越小
        if atr_ratio > 0.06:
            stake = proposed_stake * 0.6
        elif atr_ratio > 0.04:
            stake = proposed_stake * 0.7
        elif atr_ratio > 0.025:
            stake = proposed_stake * 0.85
        else:
            stake = proposed_stake

        # BTC弱势做多减半
        if side == 'long' and not btc_bull:
            stake *= 0.5

        return max(min_stake, min(stake, max_stake))

    # ========================================================================
    # 差币禁闭: 3笔中2笔止损 → 禁闭24h
    # ========================================================================

    def confirm_trade_exit(self, pair, trade, order_type, amount, rate,
                           time_in_force, exit_reason, **kwargs):
        is_loss = exit_reason in ('stop_loss', 'liquidation_risk')
        ring = self._pair_loss_ring.setdefault(pair, [])
        ring.append(is_loss)
        if len(ring) > 3:
            ring.pop(0)
        if ring.count(True) >= 2:
            jail_end = datetime.utcnow().replace(hour=0, minute=0, second=0) + timedelta(days=1)
            self._pair_jail_until[pair] = jail_end
            logger.info(f"[JAIL] {pair} 禁闭24h (最近3笔: {ring})")
        return True

    # 禁闭状态 API
    def bot_loop_start(self, **kwargs):
        now = datetime.utcnow()
        expired = [p for p, t in self._pair_jail_until.items() if now >= t]
        for p in expired:
            del self._pair_jail_until[p]
            logger.info(f"[JAIL] {p} 禁闭到期释放")
