# 服务器策略运行状态

> 更新时间: 2026-05-28 23:30 CST
> 服务器: 43.131.249.77 | 模式: 合约模拟盘 (dry_run, isolated, 20 USDT/笔)

## 当前运行

| 端口 | 策略 | K线 | 启动时间 | 交易 | 已实现盈亏 | 收益率 | 最大回撤 |
|------|------|-----|---------|------|-----------|--------|---------|
| 8081 | StrategyChanlunFutures | 3m | May 24 (已重启) | 1 | +21.87 USDT | +22.09% | 0.00% |
| 8087 | SmallCapHunterV1 | 3m | 23:17 (今日) | 0 | 0 | 0% | 0.00% |
| 8088 | FOttStrategy2 | 1h | 23:24 (今日) | 0 | 0 | 0% | 0.00% |

**8087 首笔交易**: 入场条件严格 (1d/4h/15m pytrendseries 趋势 + 3m OTT 翻转共振)，每 3 分钟检查一次，等条件触发。`startup_candle_count=200` 不是等待时间，历史数据启动时已拉取。

**8088 首笔交易**: 1h K线 + `process_only_new_candles=True`，每小时整点处理一次。23:24 启动，00:00 才会首次分析。

## 已淘汰策略 (5月28日停运)

| 端口 | 策略 | 交易 | 胜率 | 已实现盈亏 | 总收益率 | 淘汰原因 |
|------|------|------|------|-----------|---------|---------|
| 8082 | AthenaStrategyV1 | 14笔 | 80% | -2.54 USDT | -7.02% | 盈亏比差，未实现亏损大 |
| 8083 | BoneBladeStrategyV1 | 17笔 | 76.9% | -4.79 USDT | -11.54% | 亏损最大，回撤最高 |
| 8084 | GhostStrategyV1 | 2笔 | 0% | -0.84 USDT | -0.88% | 交易太少，胜率0% |
| 8085 | WhaleStrategyV1 | 1笔 | 0% | -0.39 USDT | -0.41% | 交易太少，信号不足 |
| 8086 | TrendRiderStrategy | 2笔 | - | 0 USDT (浮亏3.30) | -3.48% | 持仓不退出，出场逻辑缺陷 |

### 淘汰策略总览

| 指标 | 数值 |
|------|------|
| 淘汰策略数 | 5 |
| 累计交易 | 36 笔 |
| 累计已实现 | -8.56 USDT |
| 问题根因 | 入场信号不足 / 盈亏比差 / 出场逻辑缺陷 |

### 保留策略原因

- **8081 缠论**: 唯一正收益 (+22%)，胜率 100%，回撤 0%
- **8087 SmallCapHunterV1**: 本地回测优秀 (+223%，80%胜率，1.97%回撤)，使用 pytrendseries 替代 OTT 消除滞后
- **8088 FOttStrategy2**: 高胜率策略 (89.7%)，1h 周期稳定

## 监控

- **脚本**: `/root/freqtrade_bot/monitor_bot.sh`
- **定时**: 每 10 分钟执行 (cron: `3,13,23,33,43,53 * * * *`)
- **检查**: 端口存活 → API 响应 → profit 数据 → 日志错误
- **日志**: `/tmp/freqtrade_monitor.log` / `/tmp/freqtrade_monitor_alert.log`

## 修复记录

### 5月28日

| 问题 | 原因 | 修复 |
|------|------|------|
| 8087 一整天零交易 | `config_smallcap.json` 缺少 `initial_state: running`，bot 启动后停在 STOPPED | 添加 `initial_state`，重启 |
| 8087 报 ModuleNotFoundError | 服务器未安装 pytrendseries | `pip install pytrendseries` |
| 8081 误停 | 批量 kill 时误杀 | 用 `config.json` 重启 |
| 8088 已挂 (5.24 起) | SQLite QueuePool 超时，SIGINT 退出 | 清理 WAL 文件，重启 |
| 僵尸进程 | 旧 bot 进程卡 D 状态 | 自动消失，无需重启 |
