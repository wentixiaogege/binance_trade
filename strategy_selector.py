#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
策略自动选择工具
根据回测结果自动评分和排序
"""

strategies = [
    {
        "name": "MomentumTrendStrategy",
        "trades": 68,
        "avg_profit": 1.52,
        "total_profit": 23.45,
        "win_rate": 89.7,
        "max_drawdown": 3.21,
        "sharpe": 3.5
    },
    {
        "name": "Strategy003",
        "trades": 42,
        "avg_profit": 1.31,
        "total_profit": 18.72,
        "win_rate": 85.7,
        "max_drawdown": 4.15,
        "sharpe": 3.2
    },
    {
        "name": "Strategy001",
        "trades": 127,
        "avg_profit": 0.85,
        "total_profit": 15.33,
        "win_rate": 72.4,
        "max_drawdown": 6.82,
        "sharpe": 2.1
    }
]

def calculate_score(strategy):
    """计算综合评分"""
    # 收益分 (30%)
    profit_score = min(strategy["total_profit"] / 30 * 100, 100) * 0.30

    # 风险分 (25%)
    risk_score = (10 - min(strategy["max_drawdown"], 10)) / 10 * 100 * 0.25

    # 胜率分 (20%)
    winrate_score = strategy["win_rate"] * 0.20

    # Sharpe 分 (15%)
    sharpe_score = min(strategy["sharpe"] / 5 * 100, 100) * 0.15

    # 交易频率分 (10%)
    trades = strategy["trades"]
    if trades < 10:
        freq_score = 60
    elif 10 <= trades < 30:
        freq_score = 85
    elif 30 <= trades <= 80:
        freq_score = 100
    elif 80 < trades <= 150:
        freq_score = 80
    else:
        freq_score = 50
    freq_score *= 0.10

    total_score = profit_score + risk_score + winrate_score + sharpe_score + freq_score
    return round(total_score, 2)

def filter_strategies(strategies):
    """过滤不合格策略"""
    qualified = []
    for s in strategies:
        if (s["total_profit"] >= 5 and
            s["max_drawdown"] <= 15 and
            s["win_rate"] >= 50 and
            s["sharpe"] >= 1.0):
            qualified.append(s)
    return qualified

def rank_strategies(strategies):
    """策略排名"""
    for s in strategies:
        s["score"] = calculate_score(s)

    return sorted(strategies, key=lambda x: x["score"], reverse=True)

# 主程序
print("=" * 60)
print("策略自动选择工具")
print("=" * 60)

# 过滤
qualified = filter_strategies(strategies)
print(f"\n合格策略数: {len(qualified)} / {len(strategies)}")

# 排名
ranked = rank_strategies(qualified)

# 输出结果
print("\n策略排名（按综合评分）:")
print("-" * 60)
for i, s in enumerate(ranked, 1):
    print(f"{i}. {s['name']}")
    print(f"   总分: {s['score']} | 收益: {s['total_profit']}% | "
          f"回撤: {s['max_drawdown']}% | 胜率: {s['win_rate']}%")
    print()

# 推荐
print("=" * 60)
print("🏆 推荐策略:", ranked[0]["name"])
print(f"   综合评分: {ranked[0]['score']}")
print("=" * 60)


```

运行：
```bash
python3 strategy_selector.py