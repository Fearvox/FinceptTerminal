#!/bin/bash
# Live Monitor — 每 5 分钟刷一次仓位
cd /Users/0xvox/Documents/GitHub/FinceptTerminal/fincept-qt/scripts/data_algo
while true; do
    clear
    python3 dashboard.py 2>&1 | head -30
    echo ""
    echo "下次刷新: 5 分钟后 (Ctrl+C 退出)"
    sleep 300
done
