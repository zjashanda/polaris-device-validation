# venusws63 本轮跑通结论

- 生成时间：`2026-05-19 19:21`
- 主矩阵：`satellite/cucumber-agent-testing/debug/venusws63_matrix/20260519_190047`
- 首次唤醒重试：`satellite/cucumber-agent-testing/debug/venusws63_matrix/20260519_190047_first_wake_retry`
- 打断探测：`satellite/cucumber-agent-testing/debug/venusws63_matrix/20260519_191220_interrupt`、`satellite/cucumber-agent-testing/debug/venusws63_matrix/20260519_191605_interrupt`

## 已跑通/支持

| 功能 | 证据 |
| --- | --- |
| `false_wake_quiet_basic` | 静默窗口内未观察到唤醒 marker。 |
| `first_wake` | 主矩阵首轮单次未唤醒，但独立重试 PASS：AP wake=10，上位 wake=1；结论为支持，但需要后续做唤醒率统计。 |
| `recognition_mode_wake` | AP 和上位/云端 ASR 唤醒证据齐全。 |
| `continuous_wake_smoke` | 连续唤醒小样本 AP wake=9，上位 wake=3。 |
| `random_interval_wake_smoke` | 随机间隔唤醒小样本 AP wake=9，上位 wake=3。 |
| `basic_command_recognition` | 观察到 ASR/TTS/云端识别链路，ASR 文本为 `打开空调快`。 |
| `offline_oneshot_matrix_1000ms` | 观察到 ASR/TTS/云端识别链路，ASR 文本为 `打开空调`。 |
| `false_wake_human_speech_smoke` | 人声干扰窗口内未观察到唤醒 marker。 |
| `false_wake_white_noise_smoke` | 白噪声窗口内未观察到唤醒 marker。 |
| `wake_interrupt` | 自播窗口内唤醒注入后观察到 5 条 wake marker。 |

## 当前环境不支持

| 功能 | 原因 |
| --- | --- |
| `network_recovery_basic` | 当前 venusws63 没有连接本机 Wi-Fi，不能由电脑热点稳定断开/恢复。 |
| `clear_and_reprovision_network` | 当前缺少本机可控网络与配网入口。 |

## 需要补充配置入口

| 功能 | 缺口 |
| --- | --- |
| `half_duplex_recognition` | 需要确认 venusws63 的半双工配置入口。 |
| `full_duplex_recognition` | 需要确认 venusws63 的全双工配置入口。 |
| `volume_night_mode_config` | 需要 App/cloud API、IoT ID/token 或本地等价命令。 |

## 本轮未确认/未跑通

| 功能 | 结果 | 原因 |
| --- | --- | --- |
| `command_interrupt` | `FAIL` | 使用 `打开空调` 自播前置时，注入落在自播后未观察到新的 ASR/命令证据。 |
| `command_interrupt_weather_precondition` | `FAIL` | 使用 `今天天气怎么样` 自播前置时，注入落在自播后未观察到新的 ASR/命令证据。 |
| `cloud_set_volume` | `FAIL` | 使用疑似 venusws63 deviceId `210006741088068` 探测云控：SIT 返回业务 `501 获取设备信息异常`，UAT 返回业务 `500 processor创建connector channel发生异常`，PRO 返回 HTTP `404 no Route matched with those values`，设备日志无 cloud/volume 下发证据。 |
| `requirement_command_smoke` | `NOT_RUN_FULLY` | 本轮只跑了 `打开空调` 基础命令，未按需求词表抽样。 |
| `requirement_free_speech_smoke` | `NOT_RUN_FULLY` | 本轮未按自由说语料矩阵执行。 |
| `online_vad_special_smoke` | `PARTIAL_EVIDENCE_ONLY` | 基础命令日志已出现 online ASR/session end/云端回复，但未按完整 VAD 专项矩阵执行。 |

## 云控脚本状态

- 历史云控脚本不是没有：`tools/cloud/polaris_app_control.py` 已支持 `set-full-duplex`、`set-volume`、`set-night-mode`、`set-multi-wakeup`、`set-wakeup-threshold`、`set-wakeup-word`、`set-mic` 等动作。
- 现在应使用根目录 `polaris.local.json` 并设置 `active_project=venusws63`；旧版 `config/polaris_env.json` 只作为兼容兜底，避免控制错项目。
- 从 venusws63 日志中提取到的疑似身份为：deviceId/IoT ID `210006741088068`，MAC `60:7A:D8:1A:67:26`，SN `00000031122251761B420000004C0000`。
- 但用该 deviceId 做云控 `set-volume=5` 探测未闭环，说明还缺正确云端环境/connector/绑定信息，或该项目不走现有 base2pro 控制通道。

## 备注

- 本轮预检全部通过：声卡可用、AP/上位串口可读、控制口可写、PA 前置可执行。
- `first_wake` 出现一次单轮漏唤，独立重试通过；这说明功能支持，但不代表唤醒率稳定，后续要用压测统计。
- `basic_command_recognition` 出现 `打开空调快`，`one-shot 1000ms` 出现 `打开空调`；命令通路支持，但后续 oracle 需要做文本归一化/近似匹配。
- `command_interrupt` 两次未跑通，不能判定为已支持；优先排查自播前置时长、注入时机、半/全双工配置。

## 2026-05-20 UAT API 功能复验结论

- 已进入 UAT 后执行 API 全量矩阵，证据汇总见 `satellite/cucumber-agent-testing/references/project_profiles/venusws63_api_support_matrix.md`。
- 当前已证实可用的 API/云控功能：mic 开关、音量设置、多/唯一唤醒开关、同唤醒词设置、唤醒阈值、自然对话/全双工 v1、日志设置、唤醒音频上传开启、方言设置、夜间模式、主动交互播报、播控 resume。
- 部分支持：`set_babyCare` 业务成功但无设备侧证据；`fullDuplex_switch_new` v2 业务成功但无明确下发证据；唤醒音频上传关闭请求业务成功但未抓到设备侧 `enable 0`。
- 暂不支持：发音人音色切换。`温柔女声/稳重男声/小芳/逍遥子/一菲` 返回 `501 未找到对应的音色`；`小蓝` 虽 API 成功但设备打印 `cant find valid voiceid by xiaolan`。
- 收尾验证：已恢复 mic 开启、音量 7，并在 API 后播放“小美小美”观察到 `online_wakeup`，说明唤醒链路仍正常。

