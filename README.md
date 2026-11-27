# Xiaozhi Pi3B

树莓派 3B 上的小智 AI 语音助手（headless 部署版本）

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

## 项目来源与版权声明

本项目**拷贝并派生自开源项目 [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi)**，作者 **Junsen**，遵循 MIT License。

- 原始项目：<https://github.com/huangjunsen0406/py-xiaozhi>
- 原始版权：Copyright (c) 2025 Junsen
- 本仓库基于上游 `main` 分支快照（`合并 #333：统一设置生效规则，修复音频重载与 MQTT 会话回收`）做 Raspberry Pi 3B 部署适配与调试，核心代码版权归原作者所有。
- 依据 MIT 协议，[LICENSE](./LICENSE) 中的版权与许可声明已**完整保留**；任何分发或再修改都必须保留该声明。
- 本项目为个人适配版本，非上游官方发布。需要完整功能、桌面端或最新特性，请优先使用上游项目。

## 与上游的主要差异

- 运行环境：Raspberry Pi 3B + 64 位 Raspberry Pi OS Lite，无 GUI、无摄像头、无物理按键。
- 音频链路：使用一体式 USB 声卡（UACDemoV1.0 类型），显式指定输入/输出设备，采集增益调整到 60%~70% 以避免波形削顶。
- 日常运行使用 `gpio` 模式无界面启动，保留 `tui` 模式作为维护/配置通道。
- 启用 AEC（回声消除），支持语音播报过程中直接插话打断。
- 唤醒链路增加诊断日志（唤醒命中、音频帧中断告警、重连结果），便于排查待命状态唤醒异常。

## 功能特性

- 实时语音对话：Opus 编码、异步流式传输，直连小智官方服务端（ASR/LLM/TTS 均在云端）。
- 离线唤醒词：基于 sherpa-onnx 的本地关键词检测，默认唤醒词「你好小智」。
- 双协议：WebSocket / MQTT，支持 WSS/TLS 与自动重连。
- 多种界面模式：GUI（PySide6 + QML）/ TUI / GPIO，适配桌面、无头服务器与嵌入式设备。
- MCP 工具生态：音乐播放、音量控制、天气等模块化工具服务。
- 插件化架构：事件驱动的异步设计，支持扩展。

## 硬件与系统要求

- Raspberry Pi 3B（建议使用 64 位 Raspberry Pi OS Lite）
- USB 声卡或 USB 麦克风/音箱（板载 3.5mm 音频质量较差，不推荐）
- 可访问互联网（首次启动需完成设备激活）
- Python 3.10+

## 安装

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip portaudio19-dev libopus0 ffmpeg

git clone https://github.com/Mike-Cadence/Xiaozhi_Pi3B.git
cd Xiaozhi_Pi3B

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

说明：

- 仓库已包含唤醒词模型（`models/zh`、`models/en`），无需额外下载。
- `lgpio` 在部分 Python 版本（如 3.13）下可能编译失败。本机无物理按键时可跳过，程序会提示 GPIO 不可用，不影响语音功能。
- 无 GUI 环境无需安装 PySide6；TUI 模式需要 `textual`。

## 配置

首次启动会进入激活流程，完成后配置保存在：

```text
~/.local/share/py-xiaozhi/config/config.json
```

可通过环境变量 `XIAOZHI_DATA_DIR` 修改数据目录。

常用配置项：

| 配置项 | 说明 |
| --- | --- |
| `AUDIO_DEVICES.input_device_name` | 麦克风设备名，留空则自动检测 |
| `AUDIO_DEVICES.output_device_name` | 扬声器设备名，留空则自动检测 |
| `WAKE_WORD_OPTIONS.USE_WAKE_WORD` | 是否启用离线唤醒词 |
| `WAKE_WORD_OPTIONS.WAKE_WORD` | 唤醒词文本，默认「你好小智」 |
| `AEC_OPTIONS.ENABLED` | 是否启用回声消除（播报中打断需要开启） |
| `SYSTEM_OPTIONS.NETWORK.WEBSOCKET_URL` | 自定义 WebSocket 服务地址（默认走官方服务） |

查看系统音频设备：

```bash
arecord -l
aplay -l
```

USB 声卡建议用 `alsamixer` 将 PCM 采集增益调整到 60%~70%，避免录音削顶导致唤醒词识别率下降。

### 树莓派 USB 声卡配置（UACDemoV1.0）

`config.json` 是运行时生成的配置，包含设备激活凭据（`DEVICE_ID`、`CLIENT_ID`、`WEBSOCKET_ACCESS_TOKEN`、`MQTT_INFO` 等），已被 `.gitignore` 排除，**不要提交到仓库**。部署后请手动确认 `AUDIO_DEVICES` 与声卡实际能力一致：

```json
"AUDIO_DEVICES": {
  "input_device_name": "UACDemoV1.0: USB Audio (hw:1,0)",
  "output_device_name": "UACDemoV1.0: USB Audio (hw:1,0)",
  "input_sample_rate": 48000,
  "output_sample_rate": 48000,
  "input_channels": 1,
  "output_channels": 2,
  "opus_output_sample_rate": 24000,
  "frame_duration": 20
}
```

该声卡仅支持 48 kHz：播放 2 声道、录音 1 声道。若 `output_device_name` 保留为 `default`，或采样率/声道数与之不匹配（例如 44100 Hz、128 声道），启动播放流时会出现 `alsa_snd_pcm_start ... failed` 的 XRUN 报错。可用以下命令确认声卡能力：

```bash
cat /proc/asound/card*/stream0
```

### 刷机后恢复设备配置

代码本身已在 git 仓库中，刷机后 `git clone` 即可恢复。但以下两类设置保存在设备本地，刷机后会丢失，需要手动重新应用。

**1. 运行时配置 `~/.local/share/py-xiaozhi/config/config.json`**

该文件由激活流程生成，含设备凭据，**不要提交进仓库**。激活完成后，修改其中可公开的参数：

```bash
CFG=~/.local/share/py-xiaozhi/config/config.json
cp -p "$CFG" "$CFG.bak"
python3 - "$CFG" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))

# 唤醒词推理线程数：树莓派 3B 设为 1，避免 ONNX 线程过度订阅导致 CPU 打满
d["WAKE_WORD_OPTIONS"]["NUM_THREADS"] = 1

# USB 声卡（UACDemoV1.0）采样率与声道，按实际声卡填写
d["AUDIO_DEVICES"].update({
    "input_device_name": "UACDemoV1.0: USB Audio (hw:1,0)",
    "output_device_name": "UACDemoV1.0: USB Audio (hw:1,0)",
    "input_sample_rate": 48000,
    "output_sample_rate": 48000,
    "input_channels": 1,
    "output_channels": 2,
})

# 需要「播报中直接插话打断」时开启 AEC
# d["AEC_OPTIONS"]["ENABLED"] = True

json.dump(d, open(p, "w"), ensure_ascii=False, indent=2)
PY
```

**2. ALSA 音量（`/var/lib/alsa/asound.state`）**

刷机后声卡音量回到默认（播放 30%、采集 95%），会导致声音偏小、麦克风削顶。刷机后 card 编号可能变化，按声卡名称解析：

```bash
CARD=$(awk '/UACDemoV1.0/{print $1; exit}' /proc/asound/cards)
amixer -c "$CARD" sset PCM 100%    # 播放音量
amixer -c "$CARD" sset Mic 65%     # 采集增益，60~70% 可避免削顶
sudo alsactl store                 # 持久化，重启后自动恢复
```

验证：

```bash
amixer -c "$CARD" sget PCM | grep "Front Left"
amixer -c "$CARD" sget Mic | grep Mono
```

以上两项应用后重启程序即可生效。

## 运行

```bash
# 无界面常驻运行（树莓派推荐）
python main.py --mode gpio --protocol websocket

# 全屏终端维护界面
python main.py --mode tui --protocol websocket

# 桌面图形界面（需要 PySide6 + qasync）
python main.py --mode gui
```

首次运行如未激活，请使用 TUI 模式完成激活；激活信息会写入用户配置目录。

### systemd 开机自启（可选）

```ini
[Unit]
Description=Xiaozhi Pi3B voice assistant
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/Xiaozhi_Pi3B
ExecStart=/home/pi/Xiaozhi_Pi3B/.venv/bin/python main.py --mode gpio --protocol websocket
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## 常见问题

- **启动提示找不到音频设备**：在配置中显式填写 `AUDIO_DEVICES.input_device_name` / `output_device_name`，不要只依赖自动检测。
- **采集波形削顶、唤醒不灵**：降低 USB 声卡采集增益（60%~70%），并确认没有启用系统的自动增益。
- **播报时无法插话打断**：确认 `AEC_OPTIONS.ENABLED` 为 `true`。
- **待命状态唤醒异常**：观察日志中的 `唤醒词命中` / `唤醒触发` / `唤醒后重新开始对话` / `唤醒词检测已 N 秒未收到音频帧`，可区分是检测链路、状态机还是网络重连问题。
- **GPIO 按键不可用**：通常是 `lgpio` 未安装，不影响语音对话；无按键设备可忽略。

## 许可证

本项目遵循 [MIT License](./LICENSE)，原始版权归 **Junsen** 所有（Copyright (c) 2025 Junsen）。

本项目对上游代码的适配修改同样以 MIT 协议发布。使用、修改、分发时请保留 `LICENSE` 文件中的原始版权与许可声明，并注明来源：

> Based on [py-xiaozhi](https://github.com/huangjunsen0406/py-xiaozhi) by Junsen, licensed under the MIT License.
