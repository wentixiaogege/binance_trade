# 策略最新回测效果

## 📊 策略回测结果总览

| 策略名称 | 回测时间 | 总收益 | 交易次数 | 胜率 | 最大回撤 | 平均持仓 | 杠杆范围 | 状态 |
|---------|---------|--------|---------|------|---------|---------|---------|------|
| **StrategyChanlunFutures** | 2026-05-05 至 2026-05-10 | **+35.5%** | 3次 | 66.7% | 4.50% | 0天0小时 | 50x-100x | ✅ 最佳表现 |
| **WhaleStrategyV1** (实盘优化后) | 2026-05-17 至 2026-05-20 | **-5.91%** | 4次 | 50.0% | 37.95% | 1天14小时 | **3x-100x** | ✅ **显著改善** |
| **BoneBladeStrategyV1** | 2026-05-05 至 2026-05-19 | **+11.31%** | 7次 | 57.1% | 17.05% | 7天17小时 | 3x-20x | ✅ 正收益 |
| **FOttStrategy** | 2026-05-05 至 2026-05-19 | **+2.73%** | 29次 | 89.7% | 11.19% | 0天23小时 | 3x-30x | ✅ 高胜率 |
| **AthenaStrategyV1** | 2026-05-05 至 2026-05-19 | **-2.04%** | 6次 | 66.7% | 13.85% | 9天21小时 | 3x-20x | ⚠️ 需优化 |
| **GhostStrategyV1** | 2026-05-05 至 2026-05-19 | **-21.78%** | 41次 | 22.0% | 27.48% | 0天17小时 | 3x-25x | ❌ 表现不佳 |

## 🏆 策略表现排名

### 🥇 **冠军策略：StrategyChanlunFutures**
- **总收益**: +35.5%
- **最大回撤**: 仅 4.50%
- **特点**: 缠论策略，高杠杆但风险控制优秀
- **最佳交易**: BOME/USDT +29.99%

### 🥈 **亚军策略：WhaleStrategyV1**
- **总收益**: +14.64%
- **胜率**: 71.4%
- **特点**: OBV资金流策略，平衡收益与风险
- **最佳交易**: SOL/USDT +98.82%

### 🥉 **季军策略：BoneBladeStrategyV1**
- **总收益**: +11.31%
- **平均持仓**: 7天17小时
- **特点**: 布林带策略，中等收益水平
- **最佳交易**: SOL/USDT +99.24%

## 📈 详细分析

### 缠论策略 (StrategyChanlunFutures) 特点
| 特性 | 描述 |
|------|------|
| **策略类型** | 缠论 + EMA趋势 + ATR止损 |
| **时间框架** | 3分钟 |
| **基础策略** | 继承自 Strategy003 |
| **信号来源** | chanlun_adapter 缠论信号 |
| **杠杆机制** | 50x-100x（根据缠论级别动态调整）|
| **持仓限制** | 最大连续亏损5次停止交易 |
| **波动率控制** | ATR波动率超过8%时停止交易 |
| **多空双向** | 支持做多和做空 |
| **级别系统** | chan_top (L2+中枢) = 100x杠杆 |
| **风险控制** | 动态止损 + 止盈机制 |

## ⚠️ 重要发现

### ✅ 成功策略特征
1. **缠论策略表现最佳** - +35.5%收益，仅4.50%回撤
2. **中等杠杆更有效** - 15x-20x杠杆策略表现优于30x+
3. **风险控制关键** - 成功的策略都有严格的止损机制

### ❌ 问题策略分析
1. **GhostStrategyV1** - 高杠杆25x + 低胜率22% = 严重亏损
2. **AthenaStrategyV1** - 20x杠杆在不利市场放大亏损
3. **ETH/USDT交易问题** - 多个策略在ETH上表现不佳

### 🎯 优化建议
1. **降低GhostStrategyV1杠杆** - 从25x降至15x
2. **优化AthenaStrategyV1参数** - 调整RSI和ADX阈值
3. **增加止盈机制** - 所有策略都需要动态止盈
4. **ETH专项优化** - 针对ETH/USDT调整参数

## 📊 策略对比总结

| 维度 | 缠论策略 | OBV策略 | 布林带策略 | OTT策略 | EMA策略 | GMA策略 |
|------|----------|---------|------------|---------|---------|---------|
| **收益排名** | 🥇 1 | 🥈 2 | 🥉 3 | 4 | 5 | 6 |
| **风险等级** | 中 | 中 | 中 | 高 | 高 | 极高 |
| **胜率** | 66.7% | 71.4% | 57.1% | 89.7% | 66.7% | 22.0% |
| **适用市场** | 震荡+趋势 | 资金流趋势 | 区间突破 | 趋势跟踪 | 趋势跟踪 | 趋势跟踪 |
| **推荐杠杆** | 50x-100x | 3x-15x | 3x-20x | 3x-30x | 3x-15x | 3x-15x |

---

## 🖥️ 服务器部署状态

> 服务器: `43.131.249.77` (root) | 更新时间: 2026-05-24

### 部署总览

| 端口 | 策略 | 配置文件 | PID | 启动时间 | 数据库 | 状态 |
|------|------|----------|-----|----------|--------|------|
| 8081 | StrategyChanlunFutures | config.json | 244746 | May 16 | tradesv3.dryrun.sqlite | ✅ 运行中 |
| 8082 | AthenaStrategyV1 | config_athena.json | 580606 | May 17 | tradesv3.dryrun_athena.sqlite | ✅ 运行中 |
| 8083 | BoneBladeStrategyV1 | config_boneblade.json | 581331 | May 17 | tradesv3.dryrun_boneblade.sqlite | ✅ 运行中 |
| 8084 | GhostStrategyV1 | config_ghost.json | 581445 | May 17 | tradesv3.dryrun_ghost.sqlite | ✅ 运行中 |
| 8085 | WhaleStrategyV1 | config_whale.json | 581577 | May 17 | tradesv3.dryrun_whale.sqlite | ✅ 运行中 |
| 8086 | TrendRiderStrategy | config_trendrider.json | 446289 | May 17 | tradesv3.dryrun_trendrider.sqlite | ✅ 运行中 |
| 8088 | FOttStrategy | config_fott.json | 2917777 | May 24 | tradesv3.dryrun_fott.sqlite | ✅ 运行中 |

### 服务器环境

- **Python 环境**: `/root/freqtrade_env/bin/python3` (v3.12.3)
- **工作目录**: `/root/freqtrade_bot`
- **数据目录**: `/root/freqtrade_bot/user_data`
- **策略路径**: `user_data/strategies`
- **日志目录**: `/tmp/freqtrade_<策略名>.log`
- **统一 API 凭据**: `freqtrade` / `freqtrade123`

### 部署命令模板

```bash
# 1. 同步文件到服务器
sshpass -p 'kissmyass' scp user_data/strategies/<策略名>.py root@43.131.249.77:/root/freqtrade_bot/user_data/strategies/
sshpass -p 'kissmyass' scp user_data/config_<策略名>.json root@43.131.249.77:/root/freqtrade_bot/user_data/

# 2. 校验 MD5
md5 -q user_data/strategies/<策略名>.py
sshpass -p 'kissmyass' ssh root@43.131.249.77 "md5sum /root/freqtrade_bot/user_data/strategies/<策略名>.py"

# 3. 启动 bot (替换 <端口号> 和 <策略名>)
sshpass -p 'kissmyass' ssh root@43.131.249.77 "
  cd /root/freqtrade_bot && \
  nohup /root/freqtrade_env/bin/freqtrade trade \
    --config user_data/config_<策略名>.json \
    --strategy <策略名> \
    --strategy-path user_data/strategies \
    > /tmp/freqtrade_<策略名>.log 2>&1 &
"

# 4. 验证启动
curl -s -u freqtrade:freqtrade123 http://43.131.249.77:<端口号>/api/v1/status
```

### 新策略部署 Checklist

- [ ] 策略文件上传到 `/root/freqtrade_bot/user_data/strategies/`
- [ ] 配置文件上传到 `/root/freqtrade_bot/user_data/`（含独立 `db_url`、端口号、`bot_name`）
- [ ] MD5 校验本地与服务器文件一致
- [ ] 端口未被占用（`ss -tlnp | grep <端口>`）
- [ ] 旧进程已清理（`kill <旧PID>`）
- [ ] 新 bot 启动成功，API 返回正常
- [ ] 更新本文档的部署总览表

### 常用管理命令

```bash
# 查看所有 bot 进程
ps aux | grep freqtrade | grep -v grep

# 查看端口占用
ss -tlnp | grep -E '808[0-9]'

# 查看某个 bot 日志
tail -f /tmp/freqtrade_fott.log

# 停止某个 bot
kill <PID>

# 查看 API 状态
curl -s -u freqtrade:freqtrade123 http://localhost:<端口>/api/v1/status
curl -s -u freqtrade:freqtrade123 http://localhost:<端口>/api/v1/profit
```