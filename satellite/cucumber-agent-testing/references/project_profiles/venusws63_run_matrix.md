# venusws63 可跑通验证矩阵

## 使用目的

这个文件用于下一阶段判断当前 `venusws63` 项目哪些能力可以跑通。它只描述当前 AP+WiFi/WS63 设备，不覆盖历史 `cskwb01` 项目。

## 当前已确认基础链路

| 项 | 状态 | 证据/说明 |
| --- | --- | --- |
| 控制口 | 已通 | `COM19@115200` 可执行控制/PA 命令 |
| PA 前置 | 已通 | `COM19@115200` 执行 `uut-pa.on`、`pa-enable.set 0 17 0 1` |
| 声卡播放 | 已通 | `VID_8765&PID_5678:9_27F546DA_3_0000` 播放成功 |
| 首次唤醒 | 已通 | AP 出现 `wakeup_callback`，上位出现 `online_wakeup` |
| AP 日志 | 已通 | `COM20@921600` |
| 上位/WiFi 日志 | 已通 | `COM17@921600` |
| CP 日志 | 不适用 | 当前项目无独立 CP 串口 |

## 跑通分层

### A. 当前可以直接用调试脚本验证

这些场景不需要等待旧 Cucumber runner 改造，可以先用 `debug/manual_ap_wifi_debug/ap_wifi_smoke.py` 或同类 AP+上位脚本跑小样本：

| 能力 | 当前判断 | 断言口径 |
| --- | --- | --- |
| 首次唤醒 | 已跑通，可复跑 | 播放成功 + AP `wakeup_callback`/`Pre Wakeup` + 上位 `online_wakeup` |
| 上电/掉电控制 | 已跑通，可复跑 | 控制口回显 + AP/上位 boot/power marker |
| PA 打开与保存 | 已跑通，可复跑 | 控制口 `pa_set: 1` 或回到 `root:/$` |
| 静默误唤醒 | 可跑 | 监听窗口内 AP/上位无 wake marker，无重启 |
| 人声干扰误唤醒 | 可跑 | 播放不含唤醒词的人声，AP/上位无 wake marker |
| 白噪声误唤醒 | 可跑 | 播放白噪声，AP/上位无 wake marker |

### B. 需要 AP+上位无 CP 断言适配后跑

这些能力在产品逻辑上可以测，但不能直接套旧 `cskwb01` 的 CP/AP/ASR 三端断言，否则会因为缺 CP 被误判：

| Cucumber tag/能力 | 当前判断 | 需要改什么 |
| --- | --- | --- |
| `@recognition_mode_wake` 识别模式下唤醒 | 可测，需适配 | 二次唤醒断言改为 AP/上位 marker 或 session timer refresh |
| `@wake_latency_smoke` 唤醒响应时间 | 可测，需适配 | 只用 AP/上位首个 marker 统计 proxy 延迟 |
| `@continuous_wake_smoke` 连续唤醒 | 可测，需适配 | 成功次数按 AP/上位 wake marker 统计 |
| `@random_interval_wake_smoke` 随机间隔唤醒 | 可测，需适配 | 每轮按 AP/上位 wake marker 归因 |
| `@basic_command_recognition` 基础命令词 | 可测，需适配 | 命令证据改为 AP/上位 `online_asr_callbak`、TTS、状态闭环或 intent |
| `@requirement_command_smoke` 需求命令词 | 可测，需适配 | 同基础命令词；oracle 不清楚时不能判固件 FAIL |
| `@requirement_free_speech_smoke` 自由说 | 可测，需适配 | 以探索性 ASR/intent 证据为主 |
| `@offline_oneshot_matrix` one-shot 间隔 | 可测，需适配 | wake+command 均按 AP/上位证据判断 |
| `@online_oneshot_matrix` 在线 one-shot | 设备在线时可测 | 不做本机 Wi-Fi 控制，只验证在线 ASR/云端播报 |
| `@interrupt_prerequisite_measurement` 自播前置 | 可测，需适配 | 先找 venusws63 上稳定长播报命令 |
| `@wake_interrupt` 自播中唤醒打断 | 可测，需适配 | 注入必须落在自播窗口内，否则 `TIMING_AMBIGUOUS` |
| `@command_interrupt` 自播中识别打断 | 可测，需适配 | 自播窗口内看 AP/上位 ASR/命令响应 |
| `@online_vad_special_smoke` 在线 VAD | 设备在线时可测 | 看在线 ASR、VAD end、云端播报或文本覆盖 |

### C. 需要用户补充入口后再跑

| 能力 | 缺口 |
| --- | --- |
| 半双工识别 | 需要确认 venusws63 如何切半双工：App/cloud、本地串口命令或语音开关词 |
| 全双工识别 | 需要确认 venusws63 如何切全双工 |
| 音量调节 | 需要 App/cloud API、IoT ID/token、本地命令或可靠语音命令闭环 |
| 夜间模式 | 需要 App/cloud API、IoT ID/token 或本地命令 |
| 多唤醒/唤醒阈值/唤醒词变更 | 需要配置入口和变更后的日志/行为 oracle |
| 上电/掉电保持 | 控制口能掉电上电，但每个配置项需要先能自动设置 |

### D. 当前不跑

| 能力 | 原因 |
| --- | --- |
| `@network_recovery_basic` 联网恢复 | 当前设备没有连接本机 Wi-Fi，不能由电脑热点稳定断开/恢复 |
| 清配网/重新配网/删设备后断网 | 当前缺少本机可控网络与配网入口 |
| 依赖 CP 串口证据的旧断言 | 当前项目无 CP，必须先改断言 |
| OTA | 高风险升级类，不纳入当前 smoke |
| 8 通道 AEC/录音 | 缺少多通道录音链路 |
| 遥控器/面板按键 | 缺少自动化外设 |
| 人工听辨发音人 | 缺少客观音色判定 oracle |

## 建议执行顺序

1. 先复跑 `首次唤醒`，确认 PA、声卡、AP/上位日志链路稳定。
2. 跑 `识别模式下唤醒` 小样本，确认识别窗口内二次唤醒 marker。
3. 跑 `连续唤醒`、`随机间隔唤醒`、`唤醒响应时间 proxy`。
4. 跑 `静默/人声/白噪声误唤醒`。
5. 改 AP+上位命令断言后，再跑 `基础命令词`、`需求命令词`、`自由说`、`one-shot`。
6. 找到稳定长播报前置后，再跑 `自播打断`。

## 注意事项

- 不要直接用旧 `cskwb01` 的 CP/AP/ASR 三端断言判定 `venusws63`。
- PA 前置命令只发 `COM19@115200`，不要发到 `COM20` 或 `COM17`。
- 当前在线类场景只验证设备自身在线能力，不执行电脑热点断开/恢复。
- 没有音频回采前，唤醒响应时间只作为 proxy 趋势，不作为正式门限。

## 2026-06-01 状态更新

- WS63 云控已恢复：`check-env` PASS，真实固件账号/`vir_ver` 为 `35.03.01.01.18.26.05.04.00.01`，最终 `full-duplex=1`、`mic=on`、`night=off` 恢复 PASS。
- command-control 扩大矩阵 12/12 FAIL，root_cause=`command_or_audio_baseline`；半双工 baseline 也 FAIL，不归因为全双工单点问题。
- online VAD 扩大样本 12/12 FAIL，归因 `wake_precondition_for_online_vad`。
- offline one-shot 修复 runtime 假阴性后真机复跑 PASS，3/3 interval PASS。
- 当前设备走外部 Wi-Fi，IP `192.168.2.5`；本机热点 0 客户端时，断网/恢复仍按 `BLOCKED_NETWORK_NOT_CONTROLLABLE`。
