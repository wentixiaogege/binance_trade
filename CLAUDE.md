# CLAUDE.md - Freqtrade 交易机器人项目

## 项目概述

基于 [freqtrade](https://www.freqtrade.io/) 的加密货币自动化交易机器人，在 Binance 上进行现货和合约交易。项目目标是"先稳定跑起来，再逐步优化"。

- freqtrade 版本: 2026.1
- Python 环境: `/Users/wentixiaogege/anaconda3/envs/freqtrade/bin/python3.11`
- freqtrade 命令: `/Users/wentixiaogege/anaconda3/envs/freqtrade/bin/freqtrade`
- 工作目录: `/Users/wentixiaogege/PycharmProjects/freqtrade/freqtrde_bot`
- 项目理解文档: `项目理解.md`
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

### 启动模拟盘

```bash
cd /Users/wentixiaogege/PycharmProjects/freqtrade/freqtrde_bot

# 现货
freqtrade trade --config config.json --strategy <策略名>

# 合约
freqtrade trade --config user_data/config_futures_top10_strategy3.json --strategy Strategy003FuturesTop10 --strategy-path user_data/strategies
```

### 停止 bot

```bash
pkill -f "freqtrade trade"
# 或指定 PID: kill <PID>
```

### 查看运行状态

```bash
# 查看进程
ps aux | grep -i freqtrade | grep -v grep

# 查看 API 状态
curl -s -u <user>:<pass> http://localhost:<port>/api/v1/status

# 查看盈亏
curl -s -u <user>:<pass> http://localhost:<port>/api/v1/profit

# 查看日志
tail -f /tmp/freqtrade_dryrun.log
```

### 回测

```bash
freqtrade backtesting --config <config> --strategy <策略名> --strategy-path user_data/strategies
```

### 下载数据

```bash
freqtrade download-data --config <config> --timerange <范围> --timeframes <周期>
```

### Web UI

浏览器打开 `http://localhost:<端口>`，默认登录凭据见对应配置文件 `api_server` 部分。

## 当前运行（2026-05-04）

- 策略: `Strategy003FuturesTop10`（继承自 Strategy003）
- 配置: `user_data/config_futures_top10_strategy3.json`
- 端口: `8081`，用户名: `freqtrade`，密码: `freqtrade123`
- 模式: 合约模拟盘，10 USDT/笔，最大 5 个持仓
- 交易对: BTC, ETH, BNB, SOL, XRP, DOGE, ADA, TRX, AVAX, LINK (USDT本位)
- 日志: `/tmp/freqtrade_dryrun.log`

## 部署铁律

1. **任何时候更新策略，必须同步到本地 8081 和云端服务器**
   - 本地: `kill $(lsof -ti :8081) && nohup freqtrade trade ...`
   - 云端: `scp 策略文件 → systemctl restart freqtrade`
2. **同步后必须校验 MD5 一致**
   - `md5 -q 本地文件` vs `md5sum 云端文件`
3. **回测通过才能部署** — 永远不在未验证的策略上跑实盘
4. **部署后检查 API** — `curl localhost:8081/api/v1/profit` 确认运行
5. **云端地址**: 43.131.249.77:8081 (freqtrade/freqtrade123)
6. **本地地址**: localhost:8081 (freqtrade/freqtrade123)
7. **重置模拟盘**: 删除 `tradesv3.dryrun.sqlite` + 设置 `dry_run_wallet=100`
8. **云端必须上传所有依赖**: `chanlun.py` + `chanlun_adapter.py` + `Strategy003FuturesTop10.py` + `Strategy003.py`
