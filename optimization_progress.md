# 策略优化进度报告

## 当前版本（2026-05-15）

| 策略 | 交易 | 胜率 | 盈利 | 回撤 |
|------|------|------|------|------|
| BoneBladeStrategyV1 | 84 | 64.3% | +7.13% | 1.40% |
| WhaleStrategyV1 | 59 | 67.8% | +5.18% | 1.76% |
| GhostStrategyV1 | 95 | 61.1% | +3.62% | 3.29% |
| AthenaStrategyV1 | 74 | 68.9% | +3.08% | 1.76% |

**通用配置**：timeframe=5m, use_exit_signal=False, use_custom_stoploss=True (1h ATR), trailing_stop=False, stoploss=-0.08, stake=20 USDT, max_open_trades=6, 10 pairs

---

## BoneBladeStrategyV1

**核心逻辑**：BB中轨趋势 + ADX/DMI方向 + RSI过滤

**V1 改动**：
- 原始版本：10条件AND入场，无use_custom_stoploss，0笔交易
- V1：精简为5条件（close>BB middle + ADX>18 + +DI>-DI + RSI 35-70），关闭exit_signal，1h ATR止损
- 结果：+7.13%, 84笔, 64.3%胜率

**配置文件**：`config_boneblade.json`

---

## GhostStrategyV1

**核心逻辑**：GMA(HMA)趋势 + ADX/DMI + RSI过滤

**V1 改动**：
- 原始版本：merge_informative_pair bug导致崩溃，多轮迭代
- V1：GMA(20)趋势 + ADX>22 + +DI>-DI + RSI 35-65，关闭exit_signal，1h ATR止损
- 结果：+3.62%, 95笔, 61.1%胜率

**配置文件**：`config_ghost.json`

---

## AthenaStrategyV1

**核心逻辑**：EMA趋势 + ADX/DMI + RSI + 放量

**V1 改动**：
- 原始版本：11条件AND入场（EMA+MACD+HMA+pullback+RSI+volume），28笔/35.7%胜率
- V1：精简为7条件（EMA多头 + close>EMA_short + ADX>20 + +DI>-DI + RSI 35-70 + 放量），关闭exit_signal
- 结果：+3.08%, 74笔, 68.9%胜率

**配置文件**：`config_athena.json`

---

## WhaleStrategyV1

**核心逻辑**：OBV资金流 + EMA趋势 + ADX/DMI

**V1 改动**：
- 原始版本：OBV交叉 + near_low + 放量入场，near_high + OBV死叉出场（use_sell_signal=True, trailing_stop=True）
- V1：OBV金叉 + 放量 + EMA50趋势 + ADX>18 + +DI>-DI，关闭exit_signal，1h ATR止损
- 结果：+5.18%, 59笔, 67.8%胜率

**配置文件**：`config_whale.json`

---

**更新时间**：2026-05-15
