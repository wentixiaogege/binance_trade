#!/usr/bin/env python3
"""
实时风险监控脚本
监控当前风险敞口，超过阈值时警报
"""

import sqlite3
import requests
from datetime import datetime

DB_PATH = 'tradesv3.sqlite'
TELEGRAM_TOKEN = 'YOUR_BOT_TOKEN'
TELEGRAM_CHAT_ID = 'YOUR_CHAT_ID'

# 配置
TOTAL_CAPITAL = 10000  # 总资金
MAX_RISK_PER_TRADE = 0.02  # 单笔风险 2%
MAX_TOTAL_RISK = 0.10  # 总风险 10%
MAX_DAILY_LOSS = 0.05  # 日亏损限制 5%

def send_alert(message):
    """发送 Telegram 警报"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': f"🚨 *风险警报* 🚨\n\n{message}",
        'parse_mode': 'Markdown'
    }
    requests.post(url, data=payload)

def get_current_risk():
    """计算当前风险敞口"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 获取所有持仓
    cursor.execute("""
        SELECT
            pair,
            stake_amount,
            stop_loss_pct
        FROM trades
        WHERE is_open = 1
    """)

    open_trades = cursor.fetchall()
    conn.close()

    if not open_trades:
        return {
            'open_trades': 0,
            'total_exposure': 0,
            'total_risk': 0,
            'risk_ratio': 0
        }

    # 计算风险
    total_exposure = sum(t[1] for t in open_trades)
    total_risk_amount = sum(t[1] * abs(t[2]) for t in open_trades if t[2])
    risk_ratio = total_risk_amount / TOTAL_CAPITAL

    return {
        'open_trades': len(open_trades),
        'total_exposure': total_exposure,
        'total_risk': total_risk_amount,
        'risk_ratio': risk_ratio,
        'trades': open_trades
    }

def get_daily_pnl():
    """获取今日盈亏"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    today = datetime.now().date()
    cursor.execute("""
        SELECT SUM(close_profit_abs)
        FROM trades
        WHERE DATE(close_date) = ?
    """, (today,))

    result = cursor.fetchone()
    conn.close()

    daily_pnl = result[0] if result[0] else 0
    daily_pnl_ratio = daily_pnl / TOTAL_CAPITAL

    return daily_pnl, daily_pnl_ratio

def check_risk():
    """检查风险并警报"""
    risk_info = get_current_risk()
    daily_pnl, daily_pnl_ratio = get_daily_pnl()

    print(f"=== 风险监控报告 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"持仓数量: {risk_info['open_trades']}")
    print(f"总暴露: ${risk_info['total_exposure']:.2f}")
    print(f"总风险: ${risk_info['total_risk']:.2f} ({risk_info['risk_ratio']*100:.2f}%)")
    print(f"今日盈亏: ${daily_pnl:.2f} ({daily_pnl_ratio*100:.2f}%)")

    # 检查警报
    alerts = []

    # 1. 检查总风险敞口
    if risk_info['risk_ratio'] > MAX_TOTAL_RISK:
        alerts.append(
            f"⚠️ 总风险敞口过高: {risk_info['risk_ratio']*100:.2f}% "
            f"(限制: {MAX_TOTAL_RISK*100:.0f}%)"
        )

    # 2. 检查日亏损
    if daily_pnl_ratio < -MAX_DAILY_LOSS:
        alerts.append(
            f"⚠️ 今日亏损超过限制: {daily_pnl_ratio*100:.2f}% "
            f"(限制: -{MAX_DAILY_LOSS*100:.0f}%)\n"
            f"建议立即停止交易！"
        )

    # 3. 检查单笔风险（如果有详细数据）
    for trade in risk_info['trades']:
        if trade[2]:  # 如果有止损数据
            trade_risk = abs(trade[2])
            if trade_risk > MAX_RISK_PER_TRADE:
                alerts.append(
                    f"⚠️ {trade[0]} 单笔风险过高: {trade_risk*100:.2f}% "
                    f"(限制: {MAX_RISK_PER_TRADE*100:.0f}%)"
                )

    # 发送警报
    if alerts:
        message = "\n\n".join(alerts)
        message += f"\n\n当前持仓: {risk_info['open_trades']}"
        message += f"\n总风险: ${risk_info['total_risk']:.2f}"
        send_alert(message)
        print("\n❌ 风险警报已发送！")
    else:
        print("\n✅ 风险在可控范围内")

    print("=" * 50)

if __name__ == '__main__':
    check_risk()