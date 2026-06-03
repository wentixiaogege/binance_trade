# CLAUDE.md - Freqtrade 交易机器人项目

> **任何时候涉及策略开发、优化、回测、部署，必须首先参考 [[策略优化必知.md]] 中的规范。**
> 该文档提炼自 freqtrade-tutorials-main 全部英文教程，是所有策略工作的铁律。

## 项目概述

基于 [freqtrade](https://www.freqtrade.io/) 的加密货币自动化交易机器人，在 Binance 上进行现货和合约交易。项目目标是"先稳定跑起来，再逐步优化"。

- freqtrade 版本: 2026.1
- Python 环境: `/Users/wentixiaogege/anaconda3/envs/freqtrade/bin/python3.11`
- freqtrade 命令: `/Users/wentixiaogege/anaconda3/envs/freqtrade/bin/freqtrade`
- 工作目录: `/Users/wentixiaogege/PycharmProjects/freqtrade/freqtrde_bot`
- **策略规范**: `策略优化必知.md` — 所有策略修改必须遵守
- **时区**: 所有配置文件必须设置 `"timezone": "Asia/Shanghai"`，所有时间显示均为北京时间 (UTC+8)
- 操作手册: `readme.md`

## 关键路径

```
freqtrde_bot/
├── config.json                  # 现货配置
├── config_future.json           # 合约配置
├── user_data/
│   ├── strategies/              # 80+ 策略文件
│   ├── data/binance/            # OHLCV 历史数据
│   ├── logs/                    # 交易日志
│   └── backtest_results/        # 回测结果
├── daily_summary.py             # 每日交易总结
├── risk_manage.py               # 风控监控
└── strategy_selector.py         # 策略评分工具
```

## 配置要点

- 网络: SOCKS5 代理 `127.0.0.1:3067`（国内访问 Binance 用）
- 交易模式: 当前均为 `dry_run: true`（模拟盘）
- 通知: Telegram 框架已搭建，token/chart_id 待填写
- API 服务: 默认用户名 `freqtrade`，密码因配置而异

## 常用操作

### 启动模拟盘（服务器）

```bash
# 部署/更新策略到服务器
sshpass -p 'kissmyass' scp user_data/strategies/<策略文件>.py root@43.131.249.77:/root/freqtrade_bot/user_data/strategies/
sshpass -p 'kissmyass' scp user_data/<配置文件名>.json root@43.131.249.77:/root/freqtrade_bot/user_data/

# 在服务器上启动 bot
sshpass -p 'kissmyass' ssh root@43.131.249.77 "
  cd /root/freqtrade_bot && 
  nohup /root/freqtrade_env/bin/freqtrade trade \
    --config user_data/<配置文件名>.json \
    --strategy <策略名> \
    --strategy-path user_data/strategies \
    > /tmp/freqtrade_<策略名>.log 2>&1 &
"
```

### 停止 bot

```bash
# 停止全部（服务器）
sshpass -p 'kissmyass' ssh root@43.131.249.77 "pkill -f 'freqtrade trade'"

# 停止指定端口
sshpass -p 'kissmyass' ssh root@43.131.249.77 "kill \$(lsof -ti :<端口>)"
```

### 查看运行状态

```bash
# 查看服务器所有 bot 进程
sshpass -p 'kissmyass' ssh root@43.131.249.77 "ps aux | grep freqtrade | grep -v grep"

# 查看服务器所有端口
sshpass -p 'kissmyass' ssh root@43.131.249.77 "ss -tlnp | grep -E '808[0-9]'"

# 查看 API 状态
curl -s -u freqtrade:freqtrade123 http://43.131.249.77:<端口>/api/v1/status

# 查看盈亏
curl -s -u freqtrade:freqtrade123 http://43.131.249.77:<端口>/api/v1/profit

# 查看日志
sshpass -p 'kissmyass' ssh root@43.131.249.77 "tail -f /tmp/freqtrade_<策略名>.log"
```

### 回测（本地 CLI + FreqUI 多周期可视化）

**每次回测必须遵循此流程，确保能在 FreqUI 中查看多周期图表。**

```bash
# 1. 确保多周期数据已下载（至少：1m 3m 5m 15m 1h 4h 1d）
freqtrade download-data --config <config> --timerange <范围> --timeframes 1m 3m 5m 15m 1h 4h 1d

# 2. 启动 webserver（必须先启动，回测结果才能加载到 UI）
freqtrade webserver --config <config> &

# 3. 运行回测
freqtrade backtesting --config <config> --strategy <策略名> --strategy-path user_data/strategies --timerange <范围>

# 4. 打开 FreqUI → Backtesting → Load Results → 选择回测结果 → Visualize result
# 5. 在图表右上角 "K线周期" 下拉框切换 1m/3m/5m/15m/1h/4h/1d 等周期
# 6. 勾选 "Multi pair" → 选择多个币种 → 勾选 "叠加" 将多币种合并到一张图上对比
```

> **FreqUI 定制功能**：
> - **K线周期选择器**：`BacktestResultChart.vue` 添加了 K线周期下拉框
> - **多币种叠加**：`CandleChart.vue` + `CandleChartContainer.vue` — Multi pair 模式下勾选"叠加"，主币种显示K线，其他币种显示为彩色收盘价曲线叠加在同一张图上
> FreqUI 源码位于 `/tmp/frequi/`，构建部署命令见下方 [FreqUI 定制](#frequi-定制) 章节。

### 下载数据

```bash
freqtrade download-data --config <config> --timerange <范围> --timeframes <周期>
```

### Web UI

浏览器打开 `http://43.131.249.77:<端口>`，默认登录凭据 `freqtrade` / `freqtrade123`。

### FreqUI 定制

本地 FreqUI 源码位于 `/tmp/frequi/`（clone 自 github.com/freqtrade/frequi）。
已做定制修改（中文标签、多周期选择器等），构建部署流程：

```bash
cd /tmp/frequi
# 修改源码后...
npm run build
# 部署到 freqtrade 安装目录
INSTALLED="$(python -c 'import freqtrade.rpc.api_server.ui as _; print(_.__path__[0])')/installed"
rm -rf "$INSTALLED"/*
cp -r dist/* "$INSTALLED"/
# 重启 webserver 生效
```

## 当前运行（2026-05-24）

**所有策略均运行在云端服务器，本地不再运行 bot。** 本地仅用于回测和开发。

### 服务器部署状态

> 服务器: `43.131.249.77` (root / kissmyass) | Python: `/root/freqtrade_env/bin/python3` (v3.12.3)
> 工作目录: `/root/freqtrade_bot` | 日志: `/tmp/freqtrade_<策略名>.log`
> API 凭据: `freqtrade` / `freqtrade123`

| 端口 | 策略 | bot_name | PID | 启动时间 | 数据库 | 状态 |
|------|------|----------|-----|----------|--------|------|
| 8081 | StrategyChanlunFutures | Chanlun-8081 | 2919972 | May 24 | tradesv3.dryrun_chanlun.sqlite | ✅ |
| 8082 | AthenaStrategyV1 | Athena-8082 | 2920176 | May 24 | tradesv3.dryrun_athena.sqlite | ✅ |
| 8083 | BoneBladeStrategyV1 | BoneBlade-8083 | 2920264 | May 24 | tradesv3.dryrun_boneblade.sqlite | ✅ |
| 8084 | GhostStrategyV1 | Ghost-8084 | 2975350 | May 24 | tradesv3.dryrun_ghost.sqlite | ✅ v3 (5m) |
| 8085 | WhaleStrategyV1 | Whale-8085 | 2975707 | May 24 | tradesv3.dryrun_whale.sqlite | ✅ v3 (5m) |
| 8086 | TrendRiderStrategy | TrendRider-8086 | 2920550 | May 24 | tradesv3.dryrun_trendrider.sqlite | ✅ |
| 8088 | FOttStrategy2 | FOtt2-8088 | 2997789 | May 24 | tradesv3.dryrun_fott.sqlite | ✅ v2 |

- 模式: 合约模拟盘（dry_run），isolated 逐仓，20 USDT/笔，最大 6 个持仓
- 交易对: BTC, ETH, BNB, SOL, XRP, DOGE, ADA, TRX, AVAX, LINK (USDT本位)
- 每个 bot 均配置独立 `db_url`，数据库隔离

## 📊 最新策略回测效果（2026-05-21）

### 🏆 **策略表现排名**

| 策略名称 | 回测时间 | 总收益 | 交易次数 | 胜率 | 最大回撤 | 杠杆范围 | 状态 |
|---------|---------|--------|---------|------|---------|---------|------|
| **StrategyChanlunFutures** | 2026-05-05 至 2026-05-10 | **+35.5%** | 3次 | 66.7% | 4.50% | 50x-100x | ✅ 最佳表现 |
| **WhaleStrategyV1** (实盘优化后) | 2026-05-17 至 2026-05-20 | **-5.91%** | 4次 | 50.0% | 37.95% | **3x-100x** | ✅ **显著改善** |
| **BoneBladeStrategyV1** | 2026-05-05 至 2026-05-19 | **+11.31%** | 7次 | 57.1% | 17.05% | 3x-20x | ✅ 正收益 |
| **FOttStrategy** | 2026-05-05 至 2026-05-19 | **+2.73%** | 29次 | 89.7% | 11.19% | 3x-30x | ✅ 高胜率 |
| **AthenaStrategyV1** (优化后) | 2026-05-05 至 2026-05-19 | **-2.04%** | 6次 | 66.7% | 13.85% | 3x-15x | 🔄 **已优化** |
| **GhostStrategyV1** (优化后) | 2026-05-05 至 2026-05-19 | **-21.78%** | 41次 | 22.0% | 27.48% | 3x-15x | 🔄 **已优化** |

### 🎯 **关键优化成果**

#### 🥇 **冠军策略：缠论策略**
- **收益**: +35.5% | **回撤**: 4.50%
- **特点**: 高杠杆低风险，技术路线验证成功
- **最佳交易**: BOME/USDT +29.99%

#### 🥈 **亚军策略：WhaleStrategyV1**
- **实盘改善**: -20.86% → **-5.91%** (**+71.7%改善**)
- **NEIRO/USDT**: -10.3% → **+37.37%** (**+47.7%改善**)
- **优化内容**: 杠杆3x-100x + 动态止盈 + 严格入场条件

#### 🔄 **已优化策略**
- **AthenaStrategyV1**: 20x → 15x杠杆 + 动态止盈
- **GhostStrategyV1**: 25x → 15x杠杆 + 严格入场 + 动态止盈

---

## 部署铁律

1. **任何时候更新策略，必须同步到云端服务器**
   - 云端: `scp 策略文件 → 服务器 → 重启对应端口的 bot`
2. **同步后必须校验 MD5 一致**
   - `md5 -q 本地文件` vs `ssh root@43.131.249.77 "md5sum 云端文件"`
3. **回测通过才能部署** — 永远不在未验证的策略上跑实盘
4. **部署后检查 API** — `curl -s -u freqtrade:freqtrade123 http://43.131.249.77:<端口>/api/v1/profit` 确认运行
5. **云端地址**: 43.131.249.77:8081-8088 (freqtrade/freqtrade123)
6. **本地不再运行 bot** — 仅用于回测和策略开发
7. **重置模拟盘**: 删除对应 `tradesv3.dryrun_<策略名>.sqlite` + 重启 bot
8. **多bot必须独立数据库**: 每个bot实例必须配置独立的 `db_url`，避免共用同一个sqlite数据库导致订单数据混淆。在配置文件中添加: `"db_url": "sqlite:///tradesv3.dryrun_<策略名>.sqlite"`
9. **每个 bot 必须配置 bot_name**: 在配置文件中添加 `"bot_name": "<策略名>-<端口>"`, 方便在 Web UI 中区分
10. **服务器地址**：43.131.249.77 密码：kissmyass 账号root
11. **优化日志**: 每次优化或部署后，必须在 `optimization_logs/` 目录下写入按日期的 md 文件（如 `2026-05-16.md`），记录改了什么、回测结果、遇到的问题、下次计划
12. **重启 bot 必须优雅停止**: 
   - 先 `kill <PID>`（SIGTERM），等 5 秒让进程释放 SQLite WAL 锁
   - 确认退出后再启动新进程
   - 严禁 `pkill -9 -f freqtrade` 一刀切，会导致 D 状态僵尸进程和数据库锁死
13. **数据库锁死急救**: 如果 bot 日志报 `QueuePool limit ... connection timed out`，说明 SQLite WAL 锁残留
   - 删除 `tradesv3.dryrun_<策略名>.sqlite` + `.sqlite-wal` + `.sqlite-shm`
   - 重启该 bot（旧交易记录会丢失）

---

## 📈 技术优化总结

### ✅ **核心优化策略**
- **杠杆扩展**: 3x-100x 动态调整
- **严格入场**: 提高ADX、RSI、成交量阈值
- **动态止盈**: 盈利10%-50%分批止盈
- **风险控制**: 超买或弱势时大幅降低杠杆

### 🎯 **优化效果**
- **缠论策略**: +35.5% 收益验证成功
- **WhaleStrategyV1**: 实盘亏损减少71.7%
- **问题策略**: GhostStrategyV1和AthenaStrategyV1已优化

---

## 🚀 **下一步计划**

1. **监控运行** - 观察服务器 7 个 bot 的模拟盘表现
2. **参数微调** - 根据实际表现进一步优化策略参数
3. **策略迭代** - 对 GhostStrategyV1 和 AthenaStrategyV1 重点优化
4. **实盘准备** - 策略稳定盈利后再考虑实盘切换