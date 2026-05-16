# 策略测试计划 - 近两周效果验证
Date: 2026-05-10

## 📊 测试环境配置

### 环境要求
- **freqtrade版本**: 2026.1
- **Python环境**: `/Users/wentixiaogege/anaconda3/envs/freqtrade/bin/python3.11`
- **工作目录**: `/Users/wentixiaogege/PycharmProjects/freqtrade/freqtrde_bot`
- **数据源**: Binance USDT本位合约数据

### 测试时间范围
- **开始时间**: 2026-04-26 00:00:00
- **结束时间**: 2026-05-10 00:00:00
- **总时长**: 14天

## 🎯 测试币种
**USDT本位合约**（10大主流币种）:
- BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT, XRP/USDT
- DOGE/USDT, ADA/USDT, TRX/USDT, AVAX/USDT, LINK/USDT

## 📋 测试策略
1. **strategy_athena_optimized.py** - 稳健趋势交易
2. **strategy_bone_blade_optimized.py** - 高频波段交易  
3. **strategy_ghost_optimized.py** - 极致回撤控制
4. **strategy_whale_optimized.py** - 大资金行为捕捉

## 🚀 测试执行命令

### 1. 准备数据
```bash
# 下载近14天的合约数据
cd /Users/wentixiaogege/PycharmProjects/freqtrade/freqtrde_bot

freqtrade download-data \
  --config user_data/config_futures_top10_strategy3.json \
  --timerange 20260426-20260510 \
  --timeframes 5m \
  --pairs BTC/USDT ETH/USDT BNB/USDT SOL/USDT XRP/USDT DOGE/USDT ADA/USDT TRX/USDT AVAX/USDT LINK/USDT
```

### 2. 单个策略回测
```bash
# Athena策略测试
freqtrade backtesting \
  --config user_data/config_futures_top10_strategy3.json \
  --strategy AthenaOptimizedStrategy \
  --strategy-path user_data/strategies \
  --timerange 20260426-20260510 \
  --timeframes 5m \
  --pairs BTC/USDT ETH/USDT BNB/USDT SOL/USDT XRP/USDT DOGE/USDT ADA/USDT TRX/USDT AVAX/USDT LINK/USDT \
  --enable-position-stacking \
  --max-open-trades 10 \
  -v

# Bone Blade策略测试
freqtrade backtesting \
  --config user_data/config_futures_top10_strategy3.json \
  --strategy BoneBladeOptimizedStrategy \
  --strategy-path user_data/strategies \
  --timerange 20260426-20260510 \
  --timeframes 5m \
  --pairs BTC/USDT ETH/USDT BNB/USDT SOL/USDT XRP/USDT DOGE/USDT ADA/USDT TRX/USDT AVAX/USDT LINK/USDT \
  --enable-position-stacking \
  --max-open-trades 10 \
  -v

# Ghost策略测试
freqtrade backtesting \
  --config user_data/config_futures_top10_strategy3.json \
  --strategy GhostOptimizedStrategy \
  --strategy-path user_data/strategies \
  --timerange 20260426-20260510 \
  --timeframes 5m \
  --pairs BTC/USDT ETH/USDT BNB/USDT SOL/USDT XRP/USDT DOGE/USDT ADA/USDT TRX/USDT AVAX/USDT LINK/USDT \
  --enable-position-stacking \
  --max-open-trades 10 \
  -v

# Whale策略测试
freqtrade backtesting \
  --config user_data/config_futures_top10_strategy3.json \
  --strategy WhaleOptimizedStrategy \
  --strategy-path user_data/strategies \
  --timerange 20260426-20260510 \
  --timeframes 5m \
  --pairs BTC/USDT ETH/USDT BNB/USDT SOL/USDT XRP/USDT DOGE/USDT ADA/USDT TRX/USDT AVAX/USDT LINK/USDT \
  --enable-position-stacking \
  --max-open-trades 10 \
  -v
```

### 3. 批量测试脚本
```bash
#!/bin/bash

STRATEGIES=("AthenaOptimizedStrategy" "BoneBladeOptimizedStrategy" "GhostOptimizedStrategy" "WhaleOptimizedStrategy")
CONFIG="user_data/config_futures_top10_strategy3.json"
TIMERANGE="20260426-20260510"
TIMEFRAME="5m"
PAIRS="BTC/USDT ETH/USDT BNB/USDT SOL/USDT XRP/USDT DOGE/USDT ADA/USDT TRX/USDT AVAX/USDT LINK/USDT"

for strategy in "${STRATEGIES[@]}"; do
    echo "Testing $strategy..."
    
    freqtrade backtesting \
      --config $CONFIG \
      --strategy $strategy \
      --strategy-path user_data/strategies \
      --timerange $TIMERANGE \
      --timeframes $TIMEFRAME \
      --pairs $PAIRS \
      --enable-position-stacking \
      --max-open-trades 10 \
      -v \
      --export trades \
      --export-filename "backtest_results_${strategy}_20260426-20260510.json"
    
    echo "Completed $strategy"
    echo "=========================================="
done
```

## 📈 测试指标收集

### 核心性能指标
```python
# 每个策略需要收集的关键指标
metrics = {
    'total_trades': '总交易次数',
    'winning_trades': '盈利交易次数', 
    'losing_trades': '亏损交易次数',
    'win_rate': '胜率（%）',
    'profit_factor': '盈亏比',
    'max_drawdown': '最大回撤（%）',
    'sharpe_ratio': '夏普比率',
    'sortino_ratio': '索提诺比率',
    'calmar_ratio': '卡玛比率',
    'avg_profit': '平均盈利（%）',
    'avg_loss': '平均亏损（%）',
    'avg_holding_time': '平均持仓时间',
    'best_trade': '最佳交易（%）',
    'worst_trade': '最差交易（%）',
    'trades_per_day': '日均交易次数',
    'total_profit': '总盈利（%）',
    'total_fees': '总手续费（%）'
}
```

### 按币种统计
```python
# 每个币种单独统计
for pair in WHITELIST:
    pair_metrics = {
        'pair': '交易对',
        'trades': '交易次数',
        'win_rate': '胜率',
        'profit': '总盈利',
        'drawdown': '最大回撤',
        'avg_profit': '平均盈利'
    }
```

### 按时间段统计
```python
# 按时间段分析表现
time_segments = {
    'week1': '第一周（2026-04-26 至 2026-05-03）',
    'week2': '第二周（2026-05-03 至 2026-05-10）',
    'weekdays': '工作日表现',
    'weekends': '周末表现',
    'high_volatility': '高波动时段',
    'low_volatility': '低波动时段'
}
```

## 📊 性能评估标准

### 🎯 核心目标
1. **胜率**: > 55%（优化前基准：约50%）
2. **最大回撤**: < 15%（优化前基准：约20-25%）
3. **盈亏比**: > 1.8（优化前基准：约1.5）
4. **夏普比率**: > 1.5（优化前基准：约1.0-1.2）

### 📈 次要目标
1. **交易频率**: 保持合理水平（日均2-5次）
2. **持仓时间**: 适应合约交易特性
3. **资金曲线**: 平滑上升，减少大幅波动
4. **币种适应性**: 在10个币种上都有稳定表现

## 📝 测试报告模板

### 策略概览
```
策略名称：[策略名称]
测试周期：2026-04-26 至 2026-05-10
测试币种：10大USDT本位合约
总交易次数：[数字]
```

### 核心表现
```
胜率：[百分比]（目标：>55%）
总盈利：[百分比]
最大回撤：[百分比]（目标：<15%）
盈亏比：[数字]（目标：>1.8）
夏普比率：[数字]（目标：>1.5）
```

### 按币种表现
```
BTC/USDT: [胜率] [盈利] [交易次数]
ETH/USDT: [胜率] [盈利] [交易次数]
...
```

### 按时间段表现
```
第一周：[胜率] [盈利] [交易次数]
第二周：[胜率] [盈利] [交易次数]
```

### 优化效果对比
```
指标 | 优化前 | 优化后 | 改进幅度
胜率 | 50% | [结果] | [提升]
最大回撤 | 22% | [结果] | [降低]
盈亏比 | 1.5 | [结果] | [提升]
夏普比率 | 1.1 | [结果] | [提升]
```

## 🔄 持续监控建议

### 实时性能监控
```bash
# 运行实时bot并监控性能
freqtrade trade \
  --config user_data/config_futures_top10_strategy3.json \
  --strategy AthenaOptimizedStrategy \
  --strategy-path user_data/strategies \
  --logfile /tmp/freqtrade_athena.log \
  --api-modules rest_server \
  --api-server-password freqtrade123 \
  --api-server-port 8081
```

### 定期检查
- **每日**: 检查盈亏和持仓情况
- **每周**: 分析策略表现和参数适应性
- **每月**: 全面评估策略效果，必要时调整参数

## 💡 测试注意事项

### 数据质量
- 确保下载的数据完整无缺失
- 检查异常价格数据点
- 验证成交量数据的合理性

### 参数稳定性
- 测试不同参数组合的稳定性
- 验证参数在极端市场的表现
- 记录参数调整对性能的影响

### 风险控制
- 监控最大回撤变化
- 检查异常交易和滑点影响
- 验证止损机制的有效性

通过这个测试计划，您可以系统性地评估每个优化策略在近两周的表现，并与优化前的基准进行对比。