# 控制命令与蜂鸣器断言 Wiki

本页沉淀空调/设备控制命令的完整链路断言。结论口径：控制命令不能只看“识别到了”，也不能把“没有蜂鸣器”直接判成失败；必须按识别、控制回复、TTS/媒体播报、执行/蜂鸣器反馈四段拆开。

## 断言分段

| 链路段 | PASS 证据 | 缺失或异常归因 |
| --- | --- | --- |
| `recognition` 命令识别 | `online_asr_callbak text`、`MSpeech Cloud 3 evt asr`、本地 `offline_asr_callbak keyword` / `algo info keyword`，且文本或拼音命中本轮命令/同义词。 | 目标词未命中、拒识、串扰、声卡/PA/时序问题。 |
| `control_reply` 控制回复 | 云端 `DeviceControl` 的 `cloud.instructions.audioBroadcast` / `cloud.speech.reply`，或离线/本地明确命令关键词。 | 云环境、设备绑定、命令域、项目日志可观测性缺口。 |
| `tts_response` 播报响应 | 有有效 TTS URL、播放器 start/complete、媒体状态，或声学回采确认设备确实播报。 | `TTS url is null` / `no valid tts url` / 空 URL 时单独判播报链路 FAIL/WARN，不能覆盖控制回复结论。 |
| `actuator_feedback` 执行/蜂鸣器 | 明确 `beep` / `buzzer` / `蜂鸣` marker、项目私有执行 ACK、可用的声学回采，或人工标注 `manual_observed`。 | 无自动证据时标记 `UNKNOWN` 或 evidence gap，不能伪造 PASS。 |

## 蜂鸣器期望规则

蜂鸣器是否应该响，要结合命令类型、设备当前状态和云端/本地响应语义判断。

| 场景 | 蜂鸣器期望 | 断言处理 |
| --- | --- | --- |
| 状态发生变化，例如从关机到开机、开机到关机、模式切换、功能打开/关闭。 | `expected_if_state_changes`。 | 有明确蜂鸣/执行证据才判 actuator PASS；无证据为 UNKNOWN，不影响 recognition/control 的分段结论。 |
| no-op，例如已开机再说“打开空调”、已关闭再说“关闭空调”。 | `not_expected`。 | 响应语义含“已经/当前/已是/无需”等时，不强制蜂鸣器。 |
| 查询类，例如“查询空调模式”“查询空调联网状态”。 | `not_required`。 | 重点看识别和回复，不要求执行机构或蜂鸣器。 |
| 不支持、拒绝、请先开机、无法执行。 | `not_expected`。 | 不应把无蜂鸣当失败；应把原因归到需求/设备状态/能力支持。 |
| 媒体、新闻、问答等非设备控制。 | `not_required`。 | 重点看媒体/TTS 播放和语义响应。 |

## 同义词与误识别

- 中文命令和本地关键词可能口径不同，例如“打开空调”对应 `kong tiao kai ji`，“关闭空调”对应 `kong tiao guan ji`；断言要支持同义词和拼音归一化。
- 如果目标命令已命中，但窗口内又出现其他命令词，整体可标记 `PASS_WITH_WARNINGS`，并记录 `unexpected_recognition_warning`。
- 如果目标命令未命中，只出现其他命令词，则判 `FAIL/unexpected_recognition`，这是误识别或串扰候选。
- 未播放目标命令却出现 wake/ASR/command，要单独记录为误唤醒/误识别候选，不能静默忽略。

## 2026-05-28 真机基线结果

本轮使用 `docs/fa2命令词.txt` 的 343 条命令，执行方式为 split wake：先播“小美小美”，间隔 1600ms，再播命令词，观察 9000ms。

| 项目 | 证据目录 | PASS | PASS_WITH_WARNINGS | FAIL | 主要结论 |
| --- | --- | ---: | ---: | ---: | --- |
| `venusws63` | `satellite/cucumber-agent-testing/debug/goal_command_beep/20260528_201015/runs/venusws63_full_baseline/20260528_204530` | 161 | 174 | 8 | 多数控制命令识别和 `DeviceControl` 有证据，但 140 条左右存在 `tts_response_chain`，即 TTS URL 为空/播报未闭环。 |
| `cskwb01` | `satellite/cucumber-agent-testing/debug/goal_command_beep/20260528_201015/runs/cskwb01_full_baseline/20260528_224149` | 147 | 145 | 51 | 本地关键词可观测性强，但存在较多目标命中伴随额外识别，以及 43 条左右目标未命中的误识别/串扰候选。 |

聚合文件：`satellite/cucumber-agent-testing/debug/goal_command_beep/20260528_201015/aggregate/fa2_full_baseline_aggregate.json`。

失败子集复验后：

| 项目 | baseline FAIL | 复验/同义词 oracle 优化后稳定 FAIL | 结论 |
| --- | ---: | ---: | --- |
| `venusws63` | 8 | 0 | 8 条 baseline FAIL 复验后全部恢复为 PASS 或 PASS_WITH_WARNINGS，优先按偶发唤醒/识别时序处理。 |
| `cskwb01` | 51 | 1 | 50 条经复验或同义词规则补充后恢复；最终稳定异常为 `调温循环扇反集` 被识别成 `tiao wen xun huan shan dang wei fan ji`，疑似需求词表/同义词口径或命令文案问题。 |

最终汇总：`satellite/cucumber-agent-testing/debug/goal_command_beep/20260528_201015/aggregate/fa2_full_command_final_summary.json`。

## 当前可自动化边界

- 已能自动区分：唤醒失败、目标命令未命中、同义词/拼音命中、额外误识别、`DeviceControl` 控制回复、TTS URL 为空、媒体链路错误。
- 暂不能仅靠现有串口日志自动证明物理蜂鸣器已响；没有明确 marker 时，`actuator_feedback` 应为 `UNKNOWN`、`WARN` 或 `NOT_EXPECTED`。
- 后续若项目提供蜂鸣器日志字段、控制 ACK 协议、或可稳定回采的麦克风/声学阈值，可把 `actuator_feedback` 从 UNKNOWN 提升为自动 PASS/FAIL。
