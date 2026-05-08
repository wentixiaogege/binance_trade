#!/usr/bin/env python3
"""
每日自动总结脚本
每天定时运行，生成总结并发送到 Telegram
"""

import sqlite3
import requests
from datetime import datetime, timedelta

# 配置
DB_PATH = 'tradesv3.sqlite'
TELEGRAM_TOKEN = 'YOUR_BOT_TOKEN'
TELEGRAM_CHAT_ID = 'YOUR_CHAT_ID'

def send_telegram_message(message):
    """发送 Telegram 消息"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"发送失败: {e}")

def get_today_stats():
    """获取今日统计"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    today = datetime.now().date()
    today_start = datetime.combine(today, datetime.min.time())

    # 今日已关闭的交易
    cursor.execute("""
        SELECT
            close_profit_abs,
            close_profit,
            pair
        FROM trades
        WHERE close_date >= ?
        AND close_date IS NOT NULL
        ORDER BY close_date DESC
    """, (today_start,))

    trades = cursor.fetchall()
    conn.close()

    if not trades:
        return None

    # 统计
    total_profit = sum(t[0] for t in trades)
    win_trades = [t for t in trades if t[0] > 0]
    loss_trades = [t for t in trades if t[0] < 0]

    stats = {
        'total_trades': len(trades),
        'win_trades': len(win_trades),
        'loss_trades': len(loss_trades),
        'win_rate': len(win_trades) / len(trades) * 100 if trades else 0,
        'total_profit': total_profit,
        'best_trade': max(trades, key=lambda x: x[0]) if trades else None,
        'worst_trade': min(trades, key=lambda x: x[0]) if trades else None,
    }

    return stats

def generate_summary():
    """生成每日总结"""
    stats = get_today_stats()

    if not stats:
        return "📊 *今日交易总结*\n\n今日无已完成的交易"

    message = f"""📊 *今日交易总结* - {datetime.now().strftime('%Y-%m-%d')}

📈 *交易概览*
总交易：{stats['total_trades']} 笔
胜率：{stats['win_rate']:.1f}% ({stats['win_trades']}胜 / {stats['loss_trades']}负)

💰 *盈亏情况*
今日盈亏：{stats['total_profit']:.2f} USDT

🏆 *最佳交易*
{stats['best_trade'][2]}: +{stats['best_trade'][0]:.2f} USDT ({stats['best_trade'][1]*100:.2f}%)

📉 *最差交易*
{stats['worst_trade'][2]}: {stats['worst_trade'][0]:.2f} USDT ({stats['worst_trade'][1]*100:.2f}%)

---
_自动生成于 {datetime.now().strftime('%H:%M:%S')}_
"""

    return message

if __name__ == '__main__':
    summary = generate_summary()
    send_telegram_message(summary)
    print("每日总结已发送")