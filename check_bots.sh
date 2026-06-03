#!/bin/bash
# 检查服务器 bot 端口和 v2ray 状态
# 用法: ./check_bots.sh

HOST="root@43.131.249.77"
PASS="kissmyass"
PORTS=(8081 8082 8083 8084 8085 8086 8087 8088)
BOT_NAMES=("Chanlun" "Athena" "BoneBlade" "Ghost" "Whale" "TrendRider" "SmallCapHunter" "FOtt2")

echo "=== $(date) ==="

# 1. 检查 v2ray 状态
V2RAY_STATUS=$(sshpass -p "$PASS" ssh "$HOST" "systemctl is-active v2ray" 2>/dev/null)
echo "v2ray: $V2RAY_STATUS"

# 2. 检查每个端口
FAILED_PORTS=""
for i in "${!PORTS[@]}"; do
    PORT=${PORTS[$i]}
    NAME=${BOT_NAMES[$i]}

    PROFIT=$(curl -s --max-time 5 -u freqtrade:freqtrade123 "http://43.131.249.77:$PORT/api/v1/profit" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'trades={d[\"trade_count\"]}, profit={d[\"profit_closed_ratio\"]:.2%}')" 2>/dev/null)

    if [ -z "$PROFIT" ]; then
        echo "❌ $NAME (:$PORT) — NO RESPONSE"
        FAILED_PORTS="$FAILED_PORTS $NAME:$PORT"
    else
        echo "✅ $NAME (:$PORT) — $PROFIT"
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
