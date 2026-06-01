# 全双工命令失败控制变量分析法

## 1. 适用场景

当某条命令在全双工、识别模式、打断、自播或在线交互中失败时，不能直接判断为“命令词不支持”或“在线识别不正常”。必须用控制变量法把问题拆开，确认失败点落在音频、唤醒、模式、时序、ASR、语义、云端、媒体响应、日志解析或需求口径中的哪一层。

当前触发样例：`venusws63` 在 `online_full_duplex.example` 中，全双工开启后能观察到 `upper/COM20 online_wakeup`，但唤醒后约 1s 注入 `打开空调` 未形成 `online_asr`、命令关键词或成功响应。

## 2. 防干扰原则

- 先完成当前批量回归、压测或其他真机验证，再复现排查已知问题。
- 排查期间不要插入与当前测试无关的唤醒、命令、云控、上下电或 PA 操作。
- 每轮只改变一个变量，禁止同时改变设备、模式、命令、间隔、音量、声卡、云环境。
- 每轮保存完整 AP/CP/ASR/upper/control 日志、播放音频、执行参数和判定结果。
- 对外结论必须区分 `PASS`、`FAIL`、`BLOCKED`、`TIMING_AMBIGUOUS`、`REQUIREMENT_REVIEW`，不能用模糊口径替代证据。

## 3. 分层假设树

| 层级 | 可能原因 | 证据判定 |
| --- | --- | --- |
| 声卡/PA | 音频未播出、音量太低、PA 未开 | 播放 adapter 成功、回采 oracle 有有效 RMS、设备日志出现 wake 前置 |
| 唤醒 | 唤醒未成功或未进入在线识别态 | `online_wakeup`、`wakeup_callback`、wake SID、识别窗口状态 |
| 模式 | 全双工未真正生效或状态被恢复 | API 成功、设备日志 `fullDuplex`/timeout refresh、前后状态快照 |
| 时序 | 命令注入过早/过晚/落在唤醒播报或 guard 区 | 播放锚点、音频时长、wake 后间隔、识别超时剩余量 |
| 在线 ASR | 收到音频但无 ASR 文本 | `cloud asr with`、`Recv ASR`、`online_asr_callbak`、ASR ack |
| 命令域 | ASR 有文本但没有意图/命令关键词 | ASR 文本正确但无 command keyword 或 skill reply |
| 云端 skill | 意图返回异常、设备品类不匹配、环境授权问题 | `cloud.speech.reply` code/message、skillId、UAT/SIT/PRO、IoT 在线态 |
| 媒体/TTS | 云端有回复但播报失败 | `audioBroadcast`、player play/stop/error、HTTP timeout/retry、声学回采 |
| 日志解析 | 真实有证据但 parser 未识别 | merged.log 原文、事件图、parser coverage、mojibake 处理 |
| 需求口径 | 场景是否允许该行为不明确 | 需求文档/项目规则确认，必要时 `REQUIREMENT_REVIEW` |

## 4. 控制变量矩阵

### 4.1 同一命令，不同模式

目的：判断 `打开空调` 是命令本身失败，还是全双工模式下失败。

| 变量 | 取值 |
| --- | --- |
| 设备 | venusws63 |
| 命令 | 打开空调 |
| 模式 | 普通在线识别、半双工、全双工 |
| 唤醒后间隔 | 固定 1.5s |
| 断言 | wake 成功、online ASR 文本、命令意图/响应、无 reboot/crash |

结论口径：

- 普通/半双工 PASS，全双工 FAIL：优先定位全双工监听、时序或算法状态。
- 三种模式都 FAIL：优先定位命令域、云端 skill、设备品类或语料。
- 全双工偶现 PASS：继续做间隔/音量/轮次矩阵，判断稳定性。

### 4.2 同一模式，不同命令

目的：判断全双工下是否只有空调控制类失败。

| 类别 | 建议语料 |
| --- | --- |
| 空调控制 | 打开空调、关闭空调、调高温度、设置温度到二十六度 |
| 在线媒体 | 播放音乐、播放新闻 |
| 在线问答 | 地球为什么是圆的、红烧肉怎么做 |
| 控制反集 | 音量大点、暂停播放 |

结论口径：

- 只有空调控制失败：优先查设备品类、命令域、云端 skill 或本地控制映射。
- 所有命令都无 ASR：优先查全双工识别窗口或音频注入时序。
- 问答/媒体有 ASR 但媒体 timeout：识别链路可用，媒体链路另行归因。

### 4.3 同一命令，不同唤醒后间隔

目的：判断失败是否由唤醒播报、VAD、全双工窗口或临界超时导致。

| 间隔 | 目的 |
| --- | --- |
| 0.8s | 检查过早注入，可能被唤醒播报/前端状态吞掉 |
| 1.5s | 常规安全间隔 |
| 2.5s | 避开唤醒播报尾部 |
| 5s | 检查识别窗口中后段 |
| 超时前 guard | 只统计，不强判 |
| 超时后 | 应要求重新唤醒，否则不算窗口内识别 |

结论口径：

- 0.8s/1.0s FAIL，2.5s/5s PASS：归测试时序或唤醒播报遮挡。
- 全部间隔 FAIL 但 wake 正常：继续查 ASR/命令域。
- guard 区结果不稳定：标记 `TIMING_AMBIGUOUS`。

### 4.4 同一语料，不同音频链路

目的：排除声卡、音量、合成语音切分对识别的影响。

| 变量 | 取值 |
| --- | --- |
| 播放方式 | 唤醒+命令同文件、唤醒文件 + silence + 命令文件 |
| 音量 | 默认、较高、较低 |
| 声卡 | 项目指定声卡、默认声卡 |
| 回采 | 有条件时用 acoustic oracle 记录有效声学能量 |

结论口径：

- 只在同文件 one-shot 失败：优先查切分/间隔/合成语音。
- 换声卡或 PA 恢复后通过：归音频链路，不判设备功能失败。

### 4.5 同一设备，不同云环境/版本

目的：排除 UAT/SIT/版本授权或云端在线态导致的控制失败。

| 检查项 | 断言 |
| --- | --- |
| 设备版本 | WS63 云控目前优先 `35.03.01.01.18.26.05.04.00.01` |
| 设备环境 | 设备端 env 与 API 环境一致，如 UAT 对 UAT |
| 云端在线态 | API 返回业务成功，非 501/未上线/未登录 |
| 设备 IoT ID | 与云端控制目标一致 |

结论口径：

- API 或环境失败：`BLOCKED`，不进入全双工识别分母。
- API 成功但命令失败：继续查 ASR/命令域/媒体链路。

## 5. 最小复现顺序

1. 静默复核历史证据，确认不是 parser 或 wrapper 假阳性/假阴性。
2. 只测 `打开空调`：普通在线识别 -> 半双工 -> 全双工。
3. 在全双工下测命令矩阵：空调控制、媒体、问答。
4. 在全双工下测间隔矩阵：0.8s、1.5s、2.5s、5s、超时边界。
5. 必要时加音频链路矩阵：拆分音频、音量、声卡、回采。
6. 输出归因：前置、动作、设备证据、业务期望、最终分类。
7. 把稳定复现项注册为 failure regression 候选，经人工确认后进入回归库。

## 6. 结果报告模板

| 字段 | 内容 |
| --- | --- |
| 问题 | 如：WS63 全双工下 `打开空调` 未识别 |
| 设备/环境 | 项目、串口、声卡、版本、UAT/SIT、IoT ID |
| 前置结果 | 声卡、PA、云控、网络、全双工配置 |
| 控制变量 | 本轮只改变了什么 |
| 关键证据 | wake、ASR、command、cloud reply、media、reboot/crash |
| 结论 | PASS/FAIL/BLOCKED/TIMING_AMBIGUOUS/REQUIREMENT_REVIEW |
| 归因 | 音频/唤醒/模式/时序/ASR/命令域/云端/媒体/日志解析/需求 |
| 后续动作 | 修脚本、补需求、提固件/云端问题、注册回归用例 |

## 7. 当前 WS63 问题的实测结论

基于 2026-05-28 的控制变量复现，不能说 WS63 在线识别整体不可用：在线混合压测中 `播报今日新闻`、`播放一首歌` 有唤醒、ASR 事件和 TTS/媒体响应证据；专项矩阵中 `播报今日新闻` 在全双工 1s delay 下也能闭环。

当前更准确的归因是：`打开空调` 在 WS63 上不是单纯全双工失败，也不是单纯 1s 注入时序失败。实测 `half/full`、`1.0s/1.5s/2.5s/3.5s` 多个组合下，均能看到 wake 和 ASR 事件，但只出现空 TTS/null TTS 或底层 appliance 帧，缺少 Cucumber strict oracle 需要的在线 ASR 文本、命令关键词或有效响应 URL。因此应归为 `ac_command_or_oracle_not_full_duplex`：空调命令域处理/日志可观测性/断言口径问题，而不是“WS63 全双工整体不可识别”。

补测同域命令后，结论进一步扩大：`关闭空调`、`制热模式`、`制冷模式`、`查询空调模式` 在 WS63 全双工 1.5s 下也呈现相同模式：wake + ASR cmd + 空 TTS/null TTS，但缺少在线 ASR 文本、命令关键词、有效 TTS URL 或本轮媒体状态；同矩阵 `播报今日新闻` 仍可 PASS。这说明当前更像“WS63 空调域命令的成功 marker/日志 oracle 缺失或空调域处理没有给出可判定成功证据”，而不是单条 `打开空调` 特例。

追加日志深挖后需要修正一个断言口径：空调/设备控制类不能只靠 `TTS recv/playing with .../empty24042513.mp3` 判 PASS。2026-05-28 17:44 的半双工 split 对照中，`打开空调` 仅出现 empty TTS URL，没有 `online_asr_callbak`、本地关键词、`DeviceControl` 或 `cloud.speech.reply`；2026-05-28 17:55 的 half oneshot 也复现同样模式。因此 runner 已把空调/设备控制类收紧为：必须有匹配 ASR 文本、本地命令关键词，或 `cloud.instructions.audioBroadcast` / `cloud.speech.reply` 中的 `DeviceControl` 证据；否则归为 `device_control_oracle_gap` 或命令域失败，不能报 PASS。

历史 WS63 成功样例可作为正向对照：`20260527_181325_325_execute` 中 `空调开机/空调关机/制冷模式` 有 `algo info keyword`、`ignore local asr ... when cloud connected`、`MSpeech Cloud 3 evt`、`online_asr_callbak`、`cloud.instructions.audioBroadcast`、`mideaSkillId: DeviceControl`、`cloud.speech.reply`。这些是“控制命令正常生效”的强证据；`appliance trans/recv` 和 `process cmd` 帧在成功/失败/心跳状态中都会出现，未解析协议前只能作为辅助证据。

2026-05-28 19:27 Xshell 手动日志进一步明确了完整链路口径：识别命令、云端 DeviceControl 回复、TTS/播报、执行/蜂鸣器反馈要分段判断。该日志中 `打开空调/关闭空调` 有本地关键词、`online_asr_callbak`、`DeviceControl` 和 `cloud.speech.reply`，说明识别和云端控制回复成立；同时 `content.url` 为空并出现 `TTS url is null` / `no valid tts url`，说明播报音频未闭环。用户人工听到蜂鸣器响应可作为本次执行反馈，但自动化后续仍需接入蜂鸣器日志 marker、声学回采或人工标注，否则应记录为 actuator evidence gap。

已保存的关键证据：

- 历史全双工主流程失败：`satellite/cucumber-agent-testing/debug/runs/20260528_134058_201_execute_compiled/`
- strict 默认矩阵：`satellite/cucumber-agent-testing/debug/command_control_diagnosis/20260528_145824/`
- 1s/timing 对照矩阵：`satellite/cucumber-agent-testing/debug/command_control_diagnosis/20260528_150530/`
- 空调域补测矩阵：`satellite/cucumber-agent-testing/debug/command_control_diagnosis/20260528_171453/`
- 历史词/别名 half/full 对照矩阵：`satellite/cucumber-agent-testing/debug/command_control_diagnosis/20260528_174448/`
- half oneshot 离线口径对照矩阵：`satellite/cucumber-agent-testing/debug/command_control_diagnosis/20260528_175524/`
- 上述两轮的严格设备控制重分析：各 run 目录下 `strict_device_control_reanalysis.csv/json`
- WB01 对照历史证据：`satellite/cucumber-agent-testing/debug/goal_real_device_coverage_20260528/step1_wb01_core/half_duplex_retry_after_recognition/` 中可见 `kong tiao kai ji` 和 `online_asr_callbak, text: 打开空调`，说明 WB01 日志能给出 strict oracle 需要的命令证据。

后续遇到同类问题时，先按本文件矩阵跑 `run_command_control_diagnosis.py`，不要直接把“缺少 online_asr 文本”判成固件功能失败；如果某项目本身不输出可观测 ASR 文本，需要补项目私有 Event Graph rule 或把用例标记为 `REQUIREMENT_REVIEW/ORACLE_GAP`。

## 8. 2026-06-01 扩大样本后的修正结论

最新 WS63 专项验证把样本从单条空调命令扩大到 12 条 command-control baseline，并补了半双工对照：

- 证据目录：`satellite/cucumber-agent-testing/debug/ws63_issue_deep_validation_20260601/command_baseline_expanded/20260601_164158/`
- 前置：`check-env` PASS，串口覆盖 FULL，AP `COM20` + upper/asr `COM17` + control `COM19` 均有效。
- 结果：12/12 FAIL，root_cause=`command_or_audio_baseline`。
- 对照：半双工 baseline 同样 FAIL，因此不能继续把问题归因为“全双工模式导致命令失败”。
- 归因更新：优先按 wake/audio baseline、命令域/云端 skill、音频注入和 oracle 缺口排查；全双工只作为一个变量，不是当前主因。

后续报告中应避免使用“WS63 全双工整体不可用”的表述，改为“WS63 当前命令闭环扩大样本失败，且半/全双工均复现，需命令域/音频基线专项定位”。
