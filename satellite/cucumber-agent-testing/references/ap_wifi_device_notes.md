# AP+WiFi 新项目设备调试笔记

本文用于 WS63/AP+WiFi 这类“AP + upper/asr + control、无 CP”的项目。核心原则：先确认当前串口、声卡、云环境和联网路径，再执行真机用例；证据不足时输出 BLOCKED/EVIDENCE_GAP，不伪造 PASS。

## 当前 WS63 硬件拓扑（2026-06-01）

- AP 日志/命令串口：`COM20@921600`
- 上位/WS63/ASR 日志串口：`COM17@921600`
- 控制/上下电/PA 串口：`COM19@115200`
- 当前没有独立 CP 串口，`cp` 保持空字符串。
- 当前验证声卡：`VID_8765&PID_5678:9_27F546DA_3_0000`

## 串口使用强约束

- `uut-switch1.on` / `uut-switch1.off` 只在控制口 `COM19@115200` 输入。
- `uut-pa.on` / `pa-enable.set 0 17 0 1` 也只在控制口 `COM19@115200` 输入。
- `COM20@921600` 和 `COM17@921600` 只作为 AP / upper/asr 日志与设备命令观察口，不下发 PA 命令。

## 声卡播放不生效时的 PA 前置

如果使用声卡播放唤醒词/命令词但设备侧没有反应，先在控制口输入：

```text
uut-pa.on
pa-enable.set 0 17 0 1
```

含义：

- `uut-pa.on`：打开 PA。
- `pa-enable.set 0 17 0 1`：保存/固化 PA 相关配置。

## 云端配置硬规则

- 云控 API 前必须先执行 `check-env`，工具实际解析到设备端 `env` 与 `cloud.api_environment` 一致后才允许调用网络云控 API。
- WS63 当前验证口径：`env=1/UAT`，真实固件账号/`vir_ver` 为 `35.03.01.01.18.26.05.04.00.01`。
- HTTP 200 不等于成功，必须检查业务码；业务码异常或 env 不能确认时按 BLOCKED，而不是判固件失败。

## 断网/未联网控制硬规则

如果当前设备未连接到本机可控 Wi-Fi，先排除“电脑热点断开/恢复、配网、断网恢复”类网络编排。只要设备本身在线，在线识别类能力仍可验证，但不能把“电脑热点断开/恢复”作为设备断网前置。

断网需求必须按以下顺序选择方案：

1. DUT MAC 出现在本机热点客户端列表：可用 `hotspot_off/on/cycle`。
2. DUT 支持 `vir_ssid/vir_pwd`：可写错 SSID/密码后重启模拟未联网，再写回恢复。
3. DUT 连接外部 Wi-Fi 且外部路由器/AP 可控：走路由器电源、SSID、黑名单、ACL、防火墙等项目化入口。
4. 外部 Wi-Fi 不可控：只做在线验证，断网/恢复标记 `BLOCKED_NETWORK_NOT_CONTROLLABLE`。

## 2026-06-01 已验证证据摘要

- 设备身份：IoT ID `210006741088068`，MAC `60:7A:D8:1A:67:26`，IP `192.168.2.5`。
- 串口链路：`COM20/COM17/COM19` 恢复上电后均可用，`check-env` PASS。
- 云控链路：`set-full-duplex`、`set-mic`、`set-night-mode` 恢复动作业务成功。
- 快照链路：最终快照 coverage=1.0，unknown_fields=[]。
- 网络路径：当前 WS63 走外部 Wi-Fi；本机热点即使开启也为 0 客户端，断网恢复按 `BLOCKED_NETWORK_NOT_CONTROLLABLE`。

## 可在当前 AP+WiFi 设备上优先验证的能力

- 首次唤醒、识别模式下唤醒、唤醒响应时间、连续唤醒、随机间隔唤醒。
- 静默误唤醒、人声干扰误唤醒、白噪声误唤醒。
- 基础命令词、需求命令词、自由说 smoke，但断言要使用 AP/upper 的 ASR、TTS、DeviceControl、media 或状态闭环证据。
- one-shot 间隔矩阵、online mixed stress、wake stress、wake matrix。
- 云控配置类 smoke，前提是 `check-env` PASS。

## 当前需要专项跟进的能力

- command-control / 空调控制命令闭环：当前扩大矩阵仍按 `command_or_audio_baseline` 归因。
- online VAD：当前扩大样本按 `wake_precondition_for_online_vad` 归因。
- 自播打断：注入窗口未稳定命中时保持 `TIMING_AMBIGUOUS/BLOCKED`。
- 外部 Wi-Fi 下的断网/恢复：无外部网络控制入口时不能自动验证。

## 当前不作为本设备自动验证目标

- 依赖独立 CP 串口证据的旧断言。
- OTA、8 通道 AEC/录音、遥控器/面板按键、人工听辨发音人。
- 没有外部网络控制证据的断网/联网恢复自动化 PASS。
