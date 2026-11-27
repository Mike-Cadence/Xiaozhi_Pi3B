#!/bin/bash
# 树莓派 3B 启动脚本：等待 USB 声卡就绪后再启动，避免开机竞态导致麦克风不可用
cd "$(dirname "$0")" || exit 1

for i in $(seq 1 30); do
    grep -q "UACDemoV1.0" /proc/asound/cards && break
    sleep 1
done

exec "$(pwd)/.venv/bin/python" main.py --mode fb --protocol websocket
