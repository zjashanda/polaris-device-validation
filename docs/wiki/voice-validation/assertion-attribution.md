# 断言与归因 Wiki

资料来源：`oldTime/legacy_20260526_144646/satellite/voice-test-plan-designer`。旧目录只作为归档来源，当前方案设计和用例生成应优先读取本 Wiki 与当前 Cucumber/Runtime registry。

## 1. 统一结果状态

| 状态 | 含义 | 典型处理 |
| --- | --- | --- |
| PASS | 前置、动作、证据、禁止行为均满足当前用例断言。 | 纳入成功统计。 |
| FAIL | 前置有效、动作有效、证据明确不满足功能期望。 | 进入固件/设备/云端/算法问题分析。 |
| BLOCKED | 串口、声卡、PA、联网、云环境、资料缺失等导致用例无效。 | 不进入功能分母，先修环境或补资料。 |
| TIMING_AMBIGUOUS | 播放、识别窗口、自播窗口或超时边界无法证明注入点有效。 | 不直接判固件，调整 guard 或重测。 |
| REQUIREMENT_REVIEW | 需求口径不明确，如半双工播报中是否允许识别。 | 需要产品/需求确认后再判定。 |
| NEEDS_REVIEW | 探索性自由说、无正式 oracle、日志 marker 不完整。 | 记录证据，等待资料或人工复核。 |

## 2. 常用统计公式

- 首次唤醒率 = 首次唤醒成功次数 / 首次唤醒有效总次数 * 100%。
- 识别模式唤醒率 = 识别模式唤醒成功次数 / 识别模式唤醒有效总次数 * 100%。
- 命令词识别率 = 识别正确次数 / 有效命令词样本数 * 100%。
- 拒识率 = 识别为空次数 / 有效命令词样本数 * 100%。
- 集内串扰率 = 集内串扰次数 / 有效命令词样本数 * 100%。
- 在线 ASR 句准 = ASR 文本正确次数 / 有效在线语料总次数 * 100%。
- 全双工 ASR 句准 = 全双工 ASR 文本正确次数 / 有效全双工在线语料总次数 * 100%。
- 打断识别成功率 = 打断场景 ASR 正确次数 / 有效打断在线语料总次数 * 100%。
- 误唤醒频度 = 误唤醒次数 / 测试时长。

## 3. 归因顺序

1. 先判前置：串口 logger、声卡/PA、网络、云环境、设备模式、测试数据是否有效。
2. 再判动作：音频是否真的发出，控制口/API 是否成功，注入时机是否落在有效窗口。
3. 再判设备证据：AP/CP/ASR/upper 是否出现 wake、ASR、command、TTS、media、network、reboot/crash 等 marker。
4. 再判业务期望：文本、意图、响应、禁止行为和超时策略是否符合需求。
5. 最后分类：固件/设备、云端/API、环境、测试脚本、测试数据、需求口径。

## 4. 不能误判的情况

- 第一次唤醒失败导致命令词未识别：归唤醒/音频前置，不进入命令词分母。
- 识别模式二次唤醒超出识别超时窗口：归测试时序，不判识别模式唤醒 FAIL。
- 自播打断注入点不在自播窗口内：标记 `TIMING_AMBIGUOUS`。
- API 返回成功但设备端未切到 UAT/SIT：归环境不一致或配置链路。
- 在线媒体云端有回复但设备无播放 start/complete：归媒体链路，需要日志和回采 oracle 进一步确认。
- 未播放目标语音却出现 wake/ASR/command：记录为误唤醒/误识别候选。
- 控制命令没有蜂鸣器：不能直接判控制失败。必须先看响应语义和当前状态；已处于目标状态、查询、不支持或拒绝类响应可以不要求蜂鸣器。
- 控制命令有 `DeviceControl` 回复但 `TTS url is null`：识别/控制回复可以 PASS 或 PASS_WITH_WARNINGS，TTS/播报段单独 FAIL/WARN，不能把播报缺失混成控制失败。

## 5. 控制命令分段状态

控制命令结果必须同时输出四段：

| 分段 | 典型状态 | 说明 |
| --- | --- | --- |
| `recognition` | PASS/FAIL/WARN | 目标命令文本、拼音或本地关键词是否命中；额外识别要记录为串扰候选。 |
| `control_reply` | PASS/FAIL/WARN/NOT_REQUIRED | 云端 `DeviceControl` 或本地控制关键词是否出现。 |
| `tts_response` | PASS/FAIL/WARN/UNKNOWN/NOT_REQUIRED | TTS URL、播放器、媒体状态或声学回采是否证明播报。 |
| `actuator_feedback` | PASS/WARN/UNKNOWN/NOT_EXPECTED/NOT_REQUIRED | 蜂鸣器、执行 ACK、声学回采或人工标注是否证明物理执行。 |

详见 `docs/wiki/voice-validation/control-command-actuator-beep-assertion.md`。

## WS63 TTS 空 URL 归因

WS63 上如果观察到 `DeviceControl` / `cloud.instructions.audioBroadcast` / `cloud.speech.reply`，但同时出现 `TTS url is null` 或 `no valid tts url`，应拆分为：

- `control_reply=PASS`：控制回复有证据。
- `tts_response=FAIL/WARN`：播报链路未闭环。
- 整体通常为 `PASS_WITH_WARNINGS/tts_response_chain`，不能直接归为识别失败或控制失败。

详细模式见：`docs/wiki/voice-validation/failure-patterns/ws63-devicecontrol-empty-tts-url.md`。
