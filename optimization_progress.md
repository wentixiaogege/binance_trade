# 策略优化进度报告

## 当前状态（2026-05-15 00:46）

| 策略 | 交易 | 胜率 | 盈利 | 夏普 | 回撤 |
|------|------|------|------|------|------|
| BoneBladeOptimizedStrategy | 84 | 64.3% | +7.13% | 33.82 | 1.40% |
| BreakoutTrendStrategy | 46 | 63.0% | +2.80% | 11.37 | 1.88% |
| ADXTrendStrategy | 44 | 65.9% | +2.06% | 7.06 | 2.43% |
| ADXMomentumStrategy | 48 | 60.4% | +1.78% | 5.23 | 2.09% |

### 通用优化模式

1. **use_exit_signal = False** — 关闭出场信号，用ROI+stoploss管理出场
2. **use_custom_stoploss = True** — 1h ATR动态止损（3层：跟紧盈利→保本→初始止损）
3. **trailing_stop = False** — 不使用内置追踪止损
4. **ROI**：10%/60m → 6%/120m → 4%/240m → 2%/480m → 0
5. **stoploss**：-0.08
6. **入场条件**：4-6个AND条件（趋势线 + ADX + DMI + RSI过滤）
7. **timeframe**：5m + 1h informative (ATR止损用)
8. **stake_amount**：20 USDT，**max_open_trades**：6

---

## BoneBladeOptimizedStrategy

| 轮次 | 交易 | 胜率 | 盈利 | 夏普 | 回撤 | 关键改动 |
|------|------|------|------|------|------|----------|
| R1 | 0 | — | — | — | — | 原始版本（10条件AND入场，无use_custom_stoploss） |
| R2 | 65 | 43.1% | +0.07% | — | 1.79% | 放宽到BB<0.35+RSI<50，5条件 |
| R3 | 23 | 60.9% | -0.34% | — | 1.35% | BB<0.2+RSI 25-45+DMI，custom_stoploss太松 |
| R4 | 148 | 62.2% | +0.09% | — | 2.96% | 去掉BB条件→趋势跟随，too loose |
| **Final** | **84** | **64.3%** | **+7.13%** | **33.82** | **1.40%** | **BB middle趋势+5条件+10对+$20 stake** |

### 最终配置
- 入场（5条件AND）：close > BB middle + ADX>18 + plus_di>minus_di + RSI 35-70
- 出场：use_exit_signal=False，custom_stoploss用1h ATR×3.0
- 退出分布：ROI 79笔(94%) + force_exit 5笔(6%)
- 配置文件：`config_boneblade.json` (端口8083)

---

## BreakoutTrendStrategy

| 轮次 | 交易 | 胜率 | 盈利 | 夏普 | 回撤 | 关键改动 |
|------|------|------|------|------|------|----------|
| 原始 | — | — | — | — | — | 1h TF，15条件AND入场，IntParameter参数 |
| **Final** | **46** | **63.0%** | **+2.80%** | **11.37** | **1.88%** | **5m TF+突破信号+6条件+1h ATR止损** |

### 最终配置
- 入场（6条件AND）：EMA多头排列 + ADX>22 + DMI多头 + 突破20周期高点 + RSI 40-75
- 出场：use_exit_signal=False，custom_stoploss用1h ATR×3.0
- 配置文件：`config_breakout.json` (端口8087)

---

## ADXTrendStrategy

| 轮次 | 交易 | 胜率 | 盈利 | 夏普 | 回撤 | 关键改动 |
|------|------|------|------|------|------|----------|
| 原始 | — | — | — | — | — | 1h TF，18条件AND入场，13条件OR出场 |
| **Final** | **44** | **65.9%** | **+2.06%** | **7.06** | **2.43%** | **5m TF+5条件+关闭exit_signal+1h ATR止损** |

### 最终配置
- 入场（5条件AND）：close > EMA50 + ADX>22 + plus_di>minus_di + RSI 35-70
- 出场：use_exit_signal=False，custom_stoploss用1h ATR×3.0
- 退出分布：ROI 40笔(91%) + force_exit 4笔(9%)
- 配置文件：`config_adx.json` (端口8086)

---

## ADXMomentumStrategy

| 轮次 | 交易 | 胜率 | 盈利 | 夏普 | 回撤 | 关键改动 |
|------|------|------|------|------|------|----------|
| 原始 | — | — | — | — | — | 15m TF，IntParameter参数，confirm_trade_entry |
| **Final** | **48** | **60.4%** | **+1.78%** | **5.23** | **2.09%** | **5m TF+EMA交叉+6条件+关闭exit_signal** |

### 最终配置
- 入场（6条件AND）：EMA多头排列 + close>EMA_short + ADX>22 + DMI多头 + RSI 35-70
- 出场：use_exit_signal=False，custom_stoploss用1h ATR×3.0
- 配置文件：`config_adx.json` (端口8086)

---

## 关键经验总结

1. **use_exit_signal = False 是5m策略的关键** — 出场信号在5m上过于敏感，ROI+custom_stoploss效果更好
2. **1h ATR止损 > 5m ATR止损** — 1h ATR距离合理(1-3%)，5m ATR太小(0.1-0.5%)
3. **4-6个AND入场条件是最佳区间** — 少于4个噪音太多，多于6个交易太少
4. **趋势线 + ADX + DMI + RSI 是通用有效组合** — 4个策略都基于此模式
5. **10对pairs > 8对pairs** — 更多交易对有更多交易机会
6. **突破信号是有效的入场过滤器** — BreakoutTrendStrategy的突破20周期高点降低了噪音

---

**更新时间**：2026-05-15 00:46
**下一任务**：可考虑超参数优化（hyperopt）表现最好的策略
