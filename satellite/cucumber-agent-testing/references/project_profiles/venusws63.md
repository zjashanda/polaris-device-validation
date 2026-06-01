# venusws63 项目 Profile

## 定位

`venusws63` 是当前 WS63/AP+WiFi 真机验证项目。该项目只有 AP、上位/WS63 和控制口，没有独立 CP 串口；所有断言必须按 AP + upper/asr 证据闭环，不得强制要求 CP。

## 当前硬件拓扑（2026-06-01）

| 角色 | 串口 | 波特率 | 用途 |
| --- | --- | --- | --- |
| AP | `COM20` | `921600` | AP 日志、`version`、`deviceinfo`、`flash.get.int env` |
| upper/asr | `COM17` | `921600` | WS63/上位日志、ASR/网络/云端语音链路 |
| control | `COM19` | `115200` | 上下电、PA、复位控制口 |
| CP | 无 | - | 当前项目无独立 CP 串口，`cp` 必须留空 |

## 设备身份、声卡与唤醒词

- IoT ID：`210006741088068`
- MAC：`60:7A:D8:1A:67:26`
- 当前外部 Wi-Fi IP：`192.168.2.5`
- 声卡 key：`VID_8765&PID_5678:9_27F546DA_3_0000`
- 唤醒词：`小美小美`
- 日志唤醒 ID：`xiao mei xiao mei`

## 必须执行的 PA 前置

如果声卡播放返回 0 但设备侧没有唤醒证据，先在控制口 `COM19@115200` 输入：

```text
uut-pa.on
pa-enable.set 0 17 0 1
```

注意：PA/上下电命令只能发到 `COM19`，不能发到 `COM20` 或 `COM17`。

## 云端控制门禁

- 当前云环境：`uat`，设备端必须复查到 `env=1`。
- 真实固件账号/虚拟号已恢复到 `35.03.01.01.18.26.05.04.00.01`。
- 调 `set-full-duplex`、`set-volume`、`set-mic`、`set-night-mode`、唤醒阈值、多唤醒、主动播报等云控 API 前，必须先执行 `check-env`。
- 如果 `env` 解析失败、设备仍在 PRO/env=0、或设备端 env 与 `cloud.api_environment` 不一致，必须直接 `BLOCKED`，禁止继续调用云端配置 API。

## 2026-06-01 真机验证结论

- 串口连通：`COM20/COM17/COM19` 均可枚举并按预期波特率打开；设备上电后 `check-env` PASS。
- 云控恢复：`full-duplex=1`、`mic=on`、`night=off` 最终恢复均 HTTP/业务码 PASS。
- 快照链路：最终快照 coverage=1.0，unknown_fields=[]；before/after/diff/final 证据闭环有效。
- command-control 扩大矩阵：12/12 FAIL，root_cause=`command_or_audio_baseline`；半双工 baseline 也 FAIL，因此不能归因为全双工单点问题。
- online VAD：3 轮 x 4 候选 = 12/12 FAIL，归因为 `wake_precondition_for_online_vad`，未观察到 reboot/crash。
- offline one-shot：修复 runtime replay 假阴性后，真机复跑 PASS，3/3 interval PASS。
- 全双工变体：continuous/media/exception 仍 FAIL 在命令闭环；timeout_boundary 因 voice preset 阻塞；云控 preflow PASS 时也不能替代行为闭环。
- 在线混合压测：5 轮 FINISHED，3 PASS / 1 `WARN_NO_ONLINE_RESPONSE` / 1 `FAIL_NO_WAKE`，新闻轮 PASS。
- 打断窗口：wake/command interrupt 均因注入未稳定落入自播保护窗口，按 `BLOCKED/TIMING_AMBIGUOUS`，不判固件失败。
- 联网恢复：当前设备走外部 Wi-Fi，本机热点 0 客户端，按 `BLOCKED_NETWORK_NOT_CONTROLLABLE`；不能用电脑热点开关伪造 DUT 断网。

## 当前可验证能力

### 可直接执行并保留证据

- 首次唤醒、识别模式下唤醒、唤醒响应时间、连续唤醒、随机间隔唤醒。
- 静默/人声/白噪声误唤醒监听。
- offline/online one-shot，online mixed stress，wake stress，wake matrix。
- 云控配置类 smoke（前提是 `check-env` PASS）。
- 快照 before/after/diff/checkpoint/final 和最终汇总。

### 需要按专项问题跟进

- command-control / 设备控制命令闭环：当前不是云控或串口前置问题，需产品/固件/云端确认命令域、音频输入或 oracle。
- online VAD：需要先解决 wake precondition，再扩大样本。
- 全双工命令闭环：配置可下发不等于命令闭环通过，应继续按控制变量矩阵定位。
- 打断：需要更稳定的自播窗口和注入点，未命中窗口时不能判固件失败。
- 外部 Wi-Fi 断网恢复：需要外部路由器/AP/黑名单/电源等可控入口，否则按 BLOCKED 留证。

## AP+上位断言建议

- 唤醒 PASS：声卡播放成功 + AP 出现 `wakeup_callback` 或 `Pre Wakeup` + upper/asr 出现 `online_wakeup` 或 AP 出现 `cloud asr with <SID>`。
- 命令/语料 PASS：已唤醒 + AP/upper 出现 `online_asr_callbak`、ASR 文本、命令关键词、`DeviceControl`、TTS/media 或状态闭环证据。
- BLOCKED：声卡缺失、PA 未开、串口打不开、设备未上电、`check-env` 未通过、外部网络不可控。
- FAIL：前置满足、播放成功、采集有效，但有效窗口内缺少目标识别或响应闭环。
