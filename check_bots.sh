#!/bin/bash
# 检查服务器 bot 端口和 v2ray 状态
# 用法: ./check_bots.sh

HOST="root@43.131.249.77"
PASS="kissmyass"
# 仅监控活跃端口（8082-8086 已按需停用）
PORTS=(8081 8087 8088)
BOT_NAMES=("Chanlun" "SmallCapHunter" "FOtt2")
LOG_FILES=("freqtrade_chanpy_8081" "freqtrade_SmallCapHunter" "freqtrade_fott")

echo "=== $(date) ==="

# 1. 检查 v2ray 状态
V2RAY_STATUS=$(sshpass -p "$PASS" ssh "$HOST" "systemctl is-active v2ray" 2>/dev/null)
echo "v2ray: $V2RAY_STATUS"

# 缓存日志检查结果（只 SSH 一次）
LOGS_CACHE=$(sshpass -p "$PASS" ssh "$HOST" '
for log in freqtrade_chanpy_8081 freqtrade_SmallCapHunter freqtrade_fott; do
    f="/tmp/${log}.log"
    if [ -f "$f" ]; then
        # 最后一行有实质内容的时间戳
        last_ts=$(tail -1 "$f" 2>/dev/null | grep -oE "^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}" | head -1)
        # 最近一次 heartbeat
        last_hb=$(grep "Bot heartbeat" "$f" 2>/dev/null | tail -1 | grep -oE "^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}" | head -1)
        # 错误日志（最近 2 小时）
        recent_errors=$(grep -E "ERROR|ExchangeNotAvailable|TemporaryError|reload_markets" "$f" 2>/dev/null | tail -3)
        echo "LOGINFO|${log}|last_ts=${last_ts:-N/A}|last_hb=${last_hb:-N/A}|errors=${recent_errors:-none}"
    else
        echo "LOGINFO|${log}|last_ts=NO_FILE|last_hb=NO_FILE|errors=none"
    fi
done
' 2>/dev/null)

# 2. 检查每个端口
FAILED_PORTS=""
for i in "${!PORTS[@]}"; do
    PORT=${PORTS[$i]}
    NAME=${BOT_NAMES[$i]}
    LOG_NAME=${LOG_FILES[$i]}

    # 检查 API 响应
    API_JSON=$(curl -s --max-time 5 -u freqtrade:freqtrade123 "http://43.131.249.77:$PORT/api/v1/profit" 2>/dev/null)
    PROFIT=$(echo "$API_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'trades={d[\"trade_count\"]}, profit={d[\"profit_closed_ratio\"]:.2%}')" 2>/dev/null)

    if [ -z "$PROFIT" ]; then
        echo "❌ $NAME (:$PORT) — NO RESPONSE"
        FAILED_PORTS="$FAILED_PORTS $NAME:$PORT"
    else
        # 提取该 bot 的日志信息
        LOG_INFO=$(echo "$LOGS_CACHE" | grep "LOGINFO|${LOG_NAME}|" | head -1)
        LAST_HB=$(echo "$LOG_INFO" | grep -oE 'last_hb=[^|]+' | cut -d= -f2)
        ERRORS=$(echo "$LOG_INFO" | grep -oE 'errors=.*' | cut -d= -f2-)

        # 判断交易是否正常：有 heartbeat 且最近 2 小时内有心跳
        STATUS_ICON="✅"
        TRADE_STATUS=""
        if [ "$LAST_HB" = "N/A" ] || [ "$LAST_HB" = "NO_FILE" ]; then
            STATUS_ICON="⚠️"
            TRADE_STATUS=" [无心跳]"
        else
            HB_TS=$(date -j -f "%Y-%m-%d %H:%M" "$LAST_HB" +%s 2>/dev/null)
            NOW_TS=$(date +%s)
            DIFF_SEC=$((NOW_TS - HB_TS))
            if [ $DIFF_SEC -gt 7200 ]; then
                STATUS_ICON="⚠️"
                TRADE_STATUS=" [心跳过期: ${LAST_HB}]"
            fi
        fi
        # 检查错误日志
        if [ -n "$ERRORS" ] && [ "$ERRORS" != "none" ]; then
            STATUS_ICON="⚠️"
            TRADE_STATUS="$TRADE_STATUS [有错误]"
        fi

        echo "$STATUS_ICON $NAME (:$PORT) — $PROFIT$TRADE_STATUS"
    fi
done

# 3. 汇总
if [ -z "$FAILED_PORTS" ]; then
    echo ""
    echo "全部端口正常。"
else
    echo ""
    echo "⚠️ 异常端口: $FAILED_PORTS"
    if [ "$V2RAY_STATUS" != "active" ]; then
        echo "⛔ v2ray 也不在线 — 这是根因！"
    fi
fi
