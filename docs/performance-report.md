# 树莓派 3B 运行性能测试报告

- 测试时间：2026-11-21 11:11:40（CST）
- 测试对象：本项目 `py-xiaozhi`，GPIO 模式，WebSocket 直连小智官方服务
- 采集方式：SSH 登录设备，使用 `ps` / `top` / `/proc` / `vcgencmd` 实时采集，非估算值

## 一、测试环境

| 项目 | 配置 |
| --- | --- |
| 硬件 | Raspberry Pi 3B（4 核 ARM Cortex-A53，1 GB 内存） |
| 系统 | Debian GNU/Linux 13 (trixie)，内核 6.18.60+rpt-rpi-v8 aarch64 |
| Python | 3.13.5（项目 `.venv` 虚拟环境） |
| 音频设备 | UACDemoV1.0 USB 声卡，输入 48 kHz/1 声道，输出 48 kHz/2 声道 |
| 显示/外设 | 无 GUI、无摄像头、无实体按键 |
| 运行方式 | `nohup .venv/bin/python main.py --mode gpio --protocol websocket` 后台运行，PID 1617 |
| 散热 | 无风扇被动散热，未记录环境温度 |

关键配置：

- `WAKE_WORD_OPTIONS.NUM_THREADS = 1`（本次由 5 调整为 1）
- `AUDIO_DEVICES` 已显式指定 USB 声卡，采样率 48000 Hz
- `AEC_OPTIONS.ENABLED = false`

## 二、测试条件说明

1. **采样时设备处于一次真实语音交互过程中**：11:11:32 唤醒词触发，11:11:38 服务端 TTS 开始播放，11:11:44 回到聆听。因此本次 CPU 数据包含“唤醒检测 + 上行麦克风编码 + 下行 Opus 解码 + 播放”的综合负载，**不是纯待机空闲值**。
2. 程序已连续运行约 10 分钟，`%CPU` 的累计平均值包含启动阶段的模型加载（约 6 秒），因此略高于瞬时值。
3. 测试为**被动观测**，未运行 `stress` 等压测工具，也不包含长时间音乐播放场景。
4. 与修改前（`NUM_THREADS = 5`）的对比数据来自同一设备、相近使用方式下的采样，**并非严格同条件对照实验**。
5. 唤醒词推理基准测试在程序运行期间执行（4 核已被占用约 0.5 核），属于偏悲观的最坏情况；输入为随机噪声，每块 0.5 秒音频，连续 50 块。

## 三、内存

| 指标 | 数值 | 说明 |
| --- | --- | --- |
| VmRSS | 157,976 kB ≈ **154.3 MiB** | 实际常驻内存，占系统 17.0% |
| PSS | 151,583 kB ≈ 148.0 MiB | 共享库按比例分摊后的真实占用 |
| VmHWM | 157,976 kB | 运行峰值，与当前 RSS 相同，无上涨 |
| VmSwap | 0 kB | 未使用交换分区 |
| VmSize | 931,064 kB ≈ 909 MiB | 虚拟地址空间（ONNX 预留），非真实占用 |
| 线程数 | 9 | 调整前为 21 |

系统内存（MiB）：

| 总量 | 已用 | 空闲 | buff/cache | 可用 |
| --- | --- | --- | --- | --- |
| 905.0 | 284.5 | 340.7 | 340.0 | **620.4** |

结论：内存占用稳定，剩余约 620 MiB 可用，未触发交换，无 OOM 记录。

## 四、CPU

| 指标 | 数值 |
| --- | --- |
| 瞬时 CPU（5 秒采样，对话中） | 63.9%（约 0.64 核） |
| 进程累计平均 CPU | 53.7% |
| 待机稳态 CPU（对话结束、IDLE） | 48.9%（约 0.49 核） |
| 系统负载（1/5/15 分钟） | 0.68 / 1.05 / 2.87 |
| ALSA XRUN 报错次数 | 0 |

CPU 主要由唤醒词检测（sherpa-onnx KeywordSpotter，单线程）、音频编解码与重采样构成。

唤醒词推理基准（同一模型，不同 `NUM_THREADS`）：

| 线程数 | 每 0.5 秒音频耗时 | RTF（每 1 秒音频消耗的 CPU 秒数） |
| --- | --- | --- |
| 1 | 143.2 ms | **0.29** |
| 2 | 91.1 ms | 0.18 |
| 5 | 227.9 ms | 0.46 |

单线程 RTF 0.29，具备约 3 倍实时余量；5 线程因 4 核过度订阅与 ONNX Runtime 自旋等待，反而比单线程更慢。

修改前后对比：

| 指标 | NUM_THREADS=5 | NUM_THREADS=1 |
| --- | --- | --- |
| CPU（约） | 389% | 49% ~ 64% |
| 线程数 | 21 | 9 |
| load average | 7.60 | 0.68 |
| RSS | 158.7 MiB | 154.3 MiB |
| ALSA XRUN | 启动时出现 | 0 次 |

## 五、温度

| 指标 | 数值 | 判断 |
| --- | --- | --- |
| SoC 温度（`vcgencmd`） | **56.9 ℃** | 低于 60 ℃，健康 |
| SoC 温度（`thermal_zone0`） | 56.38 ℃ | 同一传感器，采样时刻差异 |
| `get_throttled` | `0x70000` | 历史发生过欠压/限频/降频，当前未触发 |
| 当前降频状态 | 低 4 位为 0 | 未处于欠压或降频状态 |

温度参考：< 60 ℃ 健康；60~75 ℃ 正常；80 ℃ 开始软件降频；85 ℃ 硬降频。

## 六、结论与建议

1. 当前内存、CPU、温度均处于健康区间，设备可长期运行。
2. 唤醒词检测线程数从 5 降至 1 后，CPU 下降约 8 倍，load 从 7.6 降至 0.7，音频 XRUN 消失，是本次最有效的优化。
3. `throttled=0x70000` 表明历史上出现过欠压，建议使用 5V/2.5A 电源与短而粗的线缆，避免音频卡顿复发。
4. 若后续希望进一步降低 CPU，可考虑将唤醒词推理移出 asyncio 主线程；当前无需处理。
5. 本报告为单次快照，长期稳定性建议按小时采样 `VmRSS` 观察是否存在缓慢增长。

## 附：数据采集命令

```bash
P=$(pgrep -f "[m]ain.py" | head -1)
ps -o pid,%cpu,%mem,rss,vsz,etime,nlwp,cmd -p $P
grep -E "VmPeak|VmSize|VmRSS|VmHWM|VmSwap|Threads" /proc/$P/status
grep -E "^Rss|^Pss|^Private" /proc/$P/smaps_rollup
top -b -n 2 -d 5 -p $P
free -k
cat /proc/loadavg
vcgencmd measure_temp
vcgencmd get_throttled
cat /sys/class/thermal/thermal_zone0/temp
```
