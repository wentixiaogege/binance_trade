# Athena策略优化日志 - 稳健趋势交易
Date: 2026-05-10

## 策略核心特点
**稳健趋势交易**：基于EMA、MACD、HMA的多指标综合趋势跟踪策略

## 当前版本分析
- **Base版**: EMA + MACD + HMA基础趋势跟踪
- **Adaptive版**: + 市场状态检测 + 动态止损 + 回调入场
- **Advanced版**: + 多时间框架 + DMI + 智能止损

## 核心优化建议

### 1. 入场条件优化
**当前问题**: 条件过于简单，缺乏市场状态过滤

**优化方案**:
```python
# 增加趋势强度和成交量确认
conditions = [
    (dataframe['ema_short'] > dataframe['ema_long']),  # 均线多头
    (dataframe['close'] > dataframe['hma']),           # 价格在HMA之上
    qtpylib.crossed_above(dataframe['macd'], dataframe['macdsignal']),  # MACD金叉
    (dataframe['adx'] > 25),                           # ADX趋势强度过滤
    (dataframe['volume'] > ta.SMA(dataframe['volume'], timeperiod=20)),  # 成交量确认
    (dataframe['plus_di'] > dataframe['minus_di']),    # DMI方向确认
]

# 增加回调入场机制
recent_high = ta.MAX(dataframe['high'], timeperiod=20)
pullback = (recent_high - dataframe['close']) / recent_high
conditions.append(pullback >= 0.02)  # 至少回调2%
conditions.append(pullback <= 0.05)  # 不超过5%
```

### 2. 出场条件优化
**当前问题**: 仅依赖MACD死叉，缺乏多维度退出

**优化方案**:
```python
conditions = [
    qtpylib.crossed_below(dataframe['macd'], dataframe['macdsignal']),  # MACD死叉
    qtpylib.crossed_below(dataframe['close'], dataframe['hma']),        # 价格跌破HMA
    qtpylib.crossed_above(dataframe['minus_di'], dataframe['plus_di']), # DMI转弱
    dataframe['adx'] < 20,                                             # ADX跌破阈值
    qtpylib.crossed_below(dataframe['close'], dataframe['ema_long']),   # 价格跌破EMA_long
]
```

### 3. 智能止损系统
**优化方案**:
```python
def custom_stoploss(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
    dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
    if len(dataframe) == 0:
        return self.stoploss
    
    last_candle = dataframe.iloc[-1].squeeze()
    atr = last_candle.get('atr', 0)
    if atr <= 0:
        return self.stoploss
    
    # 根据市场状态调整止损倍数
    multiplier = 2.5  # 基础止损倍数
    if last_candle.get('trend_bear', False):
        multiplier *= 0.7  # 熊市收紧止损
    if last_candle.get('vol_high', False):
        multiplier *= 0.8  # 高波动收紧止损
    if self._is_weekend(current_time):
        multiplier *= 1.2  # 周末放宽止损
    
    # 多层止损逻辑
    if current_profit > 0.10:
        trail_stop = (current_rate - atr * 1.5 - trade.open_rate) / trade.open_rate
        return max(trail_stop, self.stoploss)
    elif current_profit > 0.05:
        trail_stop = (current_rate - atr * multiplier - trade.open_rate) / trade.open_rate
        return max(trail_stop, self.stoploss)
    elif current_profit > 0.02:
        return max(0.0, self.stoploss)  # 保本止损
    else:
        base_stop = (current_rate - atr * multiplier - trade.open_rate) / trade.open_rate
        return max(base_stop, self.stoploss)
```

### 4. 多时间框架增强
**优化方案**:
```python
def informative_pairs(self):
    pairs = self.dp.current_whitelist()
    informative_pairs = [(pair, "1h") for pair in pairs]
    return informative_pairs

def populate_indicators(self, dataframe, metadata):
    # 合并1小时数据
    informative = self.dp.get_pair_dataframe(pair=metadata['pair'], timeframe="1h")
    informative['ema_long'] = ta.EMA(informative, timeperiod=200)
    informative['adx'] = ta.ADX(informative, timeperiod=14)
    dataframe = merge_informative_pair(dataframe, informative, self.timeframe, "1h", ffill=True)
    
    # 入场增加1小时趋势确认
    conditions.append(dataframe['adx_1h'] > 25)
    conditions.append(dataframe['close_1h'] > dataframe['ema_long_1h'])
```

### 5. 市场状态自适应
**优化方案**:
```python
def _detect_market_phase(self, dataframe):
    ema200 = ta.EMA(dataframe['close'], timeperiod=200)
    price_vs_ema200 = (dataframe['close'] - ema200) / ema200
    dataframe['trend_bull'] = (price_vs_ema200 > 0.03) & (dataframe['adx'] > 25)
    dataframe['trend_bear'] = (price_vs_ema200 < -0.03) & (dataframe['adx'] > 25)
    dataframe['trend_ranging'] = ~(dataframe['trend_bull'] | dataframe['trend_bear'])
    
    # 根据市场状态调整参数
    if dataframe['trend_bull'].iloc[-1]:
        self.buy_params['adx_threshold'] = 20  # 牛市放宽ADX要求
    elif dataframe['trend_bear'].iloc[-1]:
        self.buy_params['adx_threshold'] = 30  # 熊市收紧ADX要求
```

## 实施优先级
1. 🔴 **高优先级**: 增加ADX趋势强度过滤 + 智能止损系统
2. 🔴 **高优先级**: 增加多时间框架确认
3. 🟡 **中优先级**: 优化回调入场机制
4. 🟡 **中优先级**: 增强出场条件
5. 🟢 **低优先级**: 参数网格搜索优化

## 预期效果
- 胜率提升：10-15%
- 最大回撤降低：20-30%
- 风险收益比提升：30-50%