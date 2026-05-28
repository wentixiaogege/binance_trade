#!/bin/bash
# Freqtrade Bot 监控脚本
# 监控端口: 8081 (Chanlun), 8087 (SmallCapHunterV1), 8088 (FOtt2)
# 定时检查 bot 状态，异常时记录日志并发送 Telegram 通知

LOG_FILE="/tmp/freqtrade_monitor.log"
ALERT_LOG="/tmp/freqtrade_monitor_alert.log"
API_USER="freqtrade"
API_PASS="freqtrade123"
TIMEOUT=5

# 机器人配置: 端口:策略名:日志文件
declare -A BOTS
BOTS[8081]="ChanlunFutures:/tmp/freqtrade_chanlun.log"
BOTS[8087]="SmallCapHunterV1:/tmp/freqtrade_SmallCapHunterV1.log"
BOTS[8088]="FOttStrategy2:/tmp/freqtrade_fott2.log"

log_msg() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

alert() {
    local port=$1
    local msg=$2
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ALERT: Port $port - $msg" | tee -a "$ALERT_LOG" >> "$LOG_FILE"
}

check_bot() {
    local port=$1
    local IFS=':'
    local info=(${BOTS[$port]})
    local strategy="${info[0]}"
    local bot_log="${info[1]}"

    # 1. 检查端口是否在监听
    if ! ss -tlnp | grep -q ":${port} "; then
        alert "$port" "端口未监听! 策略: $strategy"
        alert "$port" "最近日志: $(tail -3 "$bot_log" 2>/dev/null | tr '\n' ' ')"
        return 1
    fi

    # 2. 使用 profit API 检测 bot 是否存活 (即使无交易也能返回数据)
    local http_code
    http_code=$(curl -s -o /tmp/monitor_${port}.json -w "%{http_code}" \
        --max-time "$TIMEOUT" -u "${API_USER}:${API_PASS}" \
        "http://localhost:${port}/api/v1/profit" 2>/dev/null)

    if [ "$http_code" != "200" ]; then
        alert "$port" "API 返回 HTTP $http_code! 策略: $strategy"
        return 1
    fi

    # 3. 解析 profit 数据
    local profit
    profit=$(python3 -c "
import json
with open('/tmp/monitor_${port}.json') as f:
    d = json.load(f)
print(f\"trades:{d['trade_count']}|closed:{d['closed_trade_count']}|profit:{d['profit_all_percent']:.2f}%|dd:{d['max_drawdown']:.4f}|winrate:{d['winrate']:.2f}\")
" 2>/dev/null)

    if [ -n "$profit" ]; then
        log_msg "Port $port ($strategy) OK - $profit"
    else
        log_msg "Port $port ($strategy) OK - HTTP 200"
    fi

    # 4. 检查最近日志是否有严重错误（排除 uvicorn.error INFO 行）
    local recent_errors
    recent_errors=$(grep -E "\- ERROR \-|\- WARNING \-|OperationalException|ModuleNotFoundError|Traceback|QueuePool" \
        "$bot_log" 2>/dev/null | tail -3)
    if [ -n "$recent_errors" ]; then
        alert "$port" "发现错误日志: $(echo "$recent_errors" | head -1 | cut -c1-200)"
    fi

    return 0
}

# 清理旧日志 (保留最近1000行)
trim_log() {
    local file=$1
    if [ -f "$file" ] && [ $(wc -l < "$file") -gt 1000 ]; then
        tail -1000 "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
    fi
}

# 主循环
log_msg "======== 监控检查开始 ========"

for port in 8081 8087 8088; do
    check_bot "$port"
done

# 清理日志
trim_log "$LOG_FILE"
trim_log "$ALERT_LOG"

log_msg "======== 监控检查结束 ========"
