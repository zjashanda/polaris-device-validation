# Polaris 支持测试项、断言与 Cucumber 用例指南

本文回答三个问题：

1. 当前这个 skill 能支持哪些测试项。
2. 每类测试项应该怎么断言，如何区分固件、设备、环境、需求和脚本问题。
3. 后续怎么读测试方案、怎么写 Cucumber 用例、怎么让脚本脱离大模型稳定执行。

适用范围：Polaris 当前仓库，包含 `cskwb01`、`venusws63` 两类项目配置，以及 `satellite/cucumber-agent-testing/` 下的 BDD + Agent Testing 框架。

## 1. 总体工作方式

### 1.1 Cucumber 在这里负责什么

Cucumber feature 文件只负责把测试意图写清楚：

```text
前置条件 -> 执行动作 -> 观察证据 -> 断言 -> 失败归因
```

真正执行时不是让大模型临时理解自然语言，而是由本地 runner 读取这些稳定文件：

- `features/polaris_voice_core.feature`：自然语言场景和 tag。
- `references/voice_core_mapping.json`：每个 tag 对应执行脚本、参数、断言、失败归因。
- `references/step_registry.json`：自然语言步骤可识别的模板。
- `references/action_registry.json`：动作如何落到本地脚本或云端 API。
- `references/assertion_registry.json`：指标、阈值、PASS/FAIL/BLOCKED 口径。
- `references/feature_contracts.json`：功能契约，说明功能意图、前置、动作、断言和归因。

所以：

- 已注册的功能，用例文本可以按约定改，脚本不需要大模型在线生成。
- 新功能或新断言，必须先进入 mapping/registry 固化一次，后续才能离线稳定执行。
- feature 的自然语言是给人看的，runtime 以 tag、registry 和 mapping 为准。

### 1.2 推荐执行入口

新人或开源使用者优先使用 task JSON：

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json --mode execute --allow-side-effects --manage-session
```

熟悉框架后可以按 tag 直接跑：

```powershell
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode dry-run --tag first_wake
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag first_wake --allow-side-effects --manage-session
```

运行模式含义：

| 模式 | 是否占串口/播放音频 | 用途 |
| --- | --- | --- |
| `plan-only` | 否 | 看测试计划和将执行哪些动作。 |
| `dry-run` | 否 | 编译 feature/mapping/task，确认参数和断言，不碰真机。 |
| `execute` | 是 | 真机执行，需要 `--allow-side-effects`，可能播放音频、占用串口、操作控制口、调用云端。 |

输出目录：

```text
satellite/cucumber-agent-testing/debug/runs/<时间戳>_<模式>/
```

关键文件：

- `execution_plan.md`：本次计划。
- `run_summary.json`：runner 汇总。
- `bdd_run_report.md`：BDD 报告。
- `logs/`：串口与动作日志。
- `session/`：受控采集 session 产物。

## 2. 先读什么，怎么读测试方案

### 2.1 资料读取顺序

处理一个新需求、新表格或新测试项时，按这个顺序读：

1. `polaris.local.json`：当前项目、串口、声卡、Wi-Fi、云环境、唤醒词。
2. `docs/wiki/voice-validation/test-item-index.md`：测试项大类、当前能力等级、已有 tag/task 和缺口。
3. `docs/wiki/voice-validation/assertion-attribution.md`：PASS/FAIL/BLOCKED/需求问题/固件问题的总规则。
4. `docs/wiki/voice-validation/packs/`：命中已有验证包时优先复用，当前已有首次唤醒、识别模式唤醒、半双工、在线全双工、基础命令词、在线混合压测和误唤醒。
5. 对应专题 Wiki：`wakeup.md`、`command.md`、`free-speech.md`、`online-recognition.md`、`false-wake.md`。
6. `docs/wiki/voice-validation/test-data-design.md`：语料、音频、样本量、标注和间隔要求。
7. `docs/knowledge/<project_id>/`：读取当前项目差异、私有 marker、配置入口和历史缺口。
8. `satellite/cucumber-agent-testing/features/polaris_voice_core.feature` 与 `references/*registry*.json`：确认当前是否已能跑。
9. `satellite/cucumber-agent-testing/tasks/examples/`：复制任务配置作为入口。
10. 如需追溯旧资料，再查看 `oldTime/legacy_20260526_144646/satellite/voice-test-plan-designer`；不要把 oldTime 作为当前执行入口。

### 2.2 每个测试项要拆成什么

不要直接把“测试项名称”当脚本。要拆成下面 10 个字段：

| 字段 | 要回答的问题 | 例子 |
| --- | --- | --- |
| 功能意图 | 这个功能想证明什么 | 识别模式下仍能再次唤醒。 |
| 前置状态 | 执行前设备必须是什么状态 | 已首次唤醒且未超时。 |
| 触发方式 | 语音、串口、云端 API、上下电还是热点 | 播放小美小美。 |
| 输入数据 | 唤醒词、命令词、自由说、噪声、间隔 | `docs/fa2命令词.txt`。 |
| 期望证据 | 哪些日志/状态代表成功 | CP/AP/ASR wake、online_asr、TTS play。 |
| 禁止行为 | 不能出现什么 | 不应重启、不应播报中误识别。 |
| 超时边界 | 观察窗口和临界保护时间 | 识别超时 15s，临界保护 1200ms。 |
| 统计口径 | 分母分子怎么算 | 唤醒率 = 成功次数 / 有效次数。 |
| BLOCKED 条件 | 哪些情况不应判固件 FAIL | 串口打不开、声卡缺失、网络离线。 |
| 失败归因 | 失败后先查哪里 | 播放、串口、前置、网络、需求、固件。 |

## 3. 项目配置与项目差异

### 3.1 根配置入口

只让新人改根目录：

```text
polaris.local.json
```

最少关注字段：

| 字段 | 用途 |
| --- | --- |
| `active_project` | 当前项目，例如 `cskwb01` 或 `venusws63`。 |
| `projects.<项目>.serial.ports` | AP/CP/ASR/upper/control 串口。 |
| `projects.<项目>.serial.baudrate` | AP/CP/ASR 日志口波特率。 |
| `projects.<项目>.serial.control_baudrate` | 控制口波特率。 |
| `common.audio.default_playback_device_key` | 声卡稳定 key；不填则使用电脑默认声卡。 |
| `common.device.wake_word` | 当前唤醒词，例如 `小美小美`。 |
| `common.device.wakeup_id` | 日志里的唤醒 ID，例如 `xiao mei xiao mei`。 |
| `projects.<项目>.network` | 联网/断网用例的 Wi-Fi 信息。 |
| `projects.<项目>.cloud` | UAT/SIT/PRO API 环境，必须和设备端环境一致。 |
| `projects.<项目>.serial.control_preconditions` | PA/声卡链路前置命令，例如 `uut-pa.on`。 |

PA 注意事项：如果声卡播放返回 0，但设备完全没有唤醒证据，先在控制口执行：

```text
uut-pa.on
pa-enable.set 0 17 0 1
```

这两个命令必须发到 `control` 串口，不要发到 AP/CP/ASR 日志口。

### 3.2 当前项目支持情况

| 项目 | 拓扑 | 当前可跑重点 | 需要注意 |
| --- | --- | --- | --- |
| `cskwb01` | AP + CP + ASR + control | 唤醒、命令词、在线交互、云控设置、联网恢复、压测、重启监控 | 断言可使用 CP/AP/ASR 三端互证。 |
| `venusws63` | AP + upper/WS63 + control，无 CP | 唤醒、命令词/ASR、在线交互、媒体/TTS、稳定性 | `cp` 留空；CP 相关断言要降级为 AP + upper/WS63 证据。 |

2026-06-01 当前参考映射：

- WB01：AP `COM13@921600`、CP `COM14@921600`、WB01/ASR `COM11@921600`、control `COM12@115200`。
- WS63：AP `COM20@921600`、upper/asr `COM17@921600`、control `COM19@115200`，声卡 `VID_8765&PID_5678:9_27F546DA_3_0000`，env=`1/UAT`。
- WS63 云控已恢复，但 command-control、online VAD、部分全双工命令闭环和 interrupt 仍按专项结果输出，不得写成“全功能 PASS”。

API/云控类用例必须确认设备端环境：

| 目标环境 | 设备端串口命令 | API 配置 |
| --- | --- | --- |
| UAT | `flash.set.int env@1` 后 `reboot` | `cloud.api_environment=uat` |
| SIT | `flash.set.int env@2` 后 `reboot` | `cloud.api_environment=sit` |
| PRO | `flash.set.int env@0` 后 `reboot` | `cloud.api_environment=pro` |

只改 API 环境、不切设备端环境，容易出现“接口返回成功但设备不生效”。2026-06-01 起，云控 API 前必须由工具实际解析到设备端 `env` 与 `cloud.api_environment` 一致；解析不到、设备仍在 PRO/env=0、或切换后未重启/复查成功时，必须 `BLOCKED`，禁止继续调用网络云端配置 API。

## 4. 当前已落地可执行的 Cucumber 测试项

下面这些 tag 已在 `polaris_voice_core.feature`、`voice_core_mapping.json` 和相关 runner 中沉淀，可通过 Cucumber 框架触发。

### 4.1 唤醒与会话

| tag | 测试项 | 怎么执行 | 主断言 | 重点归因 |
| --- | --- | --- | --- | --- |
| `first_wake` | 首次唤醒 | 设备待唤醒，播放唤醒词，观察一段窗口 | 播放返回 0；CP/AP/ASR 或 AP/upper 出现 wake marker | 播放失败=声卡/环境；串口无日志=logger；播放成功无 wake=音频链路/麦克风/固件；部分端口缺失=定位模块。 |
| `recognition_mode_wake` | 识别模式下唤醒 | 第一次唤醒成功后，在识别超时窗口内再次播放唤醒词 | 第一次唤醒成功；第二次播放未超时；窗口内出现再次唤醒证据 | 第一次失败不算本项失败；第二次越过超时是测试时序问题；窗口内无证据才进入固件/需求判断。 |
| `wake_latency_smoke` | 唤醒响应时间小样本 | 多轮播放唤醒词，取播放时间线和 wake marker 时间 | 仅统计唤醒成功样本；输出平均/最大/最小/候选超阈值 | 没有正式起点/阈值时只报告，不因耗时偏大直接 FAIL。 |
| `continuous_wake_smoke` | 连续唤醒稳定性 | 连续播放唤醒词，监控唤醒、日志中断、重启 | 连续唤醒证据；无 reboot/crash；串口不断流 | 连续未唤醒、日志停止、重启要分开归因，不混入首次唤醒率。 |
| `random_interval_wake_smoke` | 随机间隔唤醒 | 按随机间隔播放唤醒词 | 逐轮 PASS/FAIL/BLOCKED；输出唤醒率、连续失败、异常状态 | 间隔压到识别超时边界时标记时序风险，不直接判固件。 |

### 4.2 命令词、自由说、one-shot

| tag | 测试项 | 怎么执行 | 主断言 | 重点归因 |
| --- | --- | --- | --- | --- |
| `basic_command_recognition` | 基础命令词识别 | 对命令词文件逐条执行“先唤醒再命令” | 每条播放成功；有唤醒；有 ASR/关键词/命令证据 | 词表不可读=数据问题；未唤醒=前置/音频；有唤醒无 ASR=ASR/固件；文本不一致=词表/需求/oracle。 |
| `requirement_command_smoke` | 需求命令词小样本 | 从需求抽取命令候选，抽样执行 | 唤醒闭环；命令/ASR/TTS 证据；oracle 状态明确 | 无正式 oracle 时不能直接判固件 FAIL。 |
| `requirement_free_speech_smoke` | 需求自由说小样本 | 从需求抽取自由说候选，先唤醒后播放 | 唤醒；ASR/intent/云端反馈；探索性标记 | 自由说缺少正式期望时标记 `NEEDS_REVIEW`。 |
| `offline_oneshot_matrix` | 离线 one-shot 间隔矩阵 | 唤醒词后按 500/800/1000/1500ms 等间隔播放离线命令 | 每个间隔有唤醒与命令识别闭环 | 分清唤醒失败、命令未识别、间隔策略、ASR/固件问题。 |
| `online_oneshot_matrix` | 在线 one-shot 间隔矩阵 | 设备在线，唤醒词后按间隔播放在线语料 | 设备在线；唤醒；在线 ASR 或云端 TTS/media 证据 | ensure-online 失败=联网前置；有唤醒无在线=在线链路/云端/固件。 |

### 4.3 半双工、全双工、VAD

| tag | 测试项 | 怎么执行 | 主断言 | 重点归因 |
| --- | --- | --- | --- | --- |
| `half_duplex_recognition` | 半双工识别 | 云端或本地切半双工，每条在线语料前都重新唤醒 | 配置应用成功；唤醒成功；命令/ASR 闭环；播报中不应违规识别 | 配置未生效=云端/网络/配置；播报中是否应响应不清楚=需求问题。 |
| `full_duplex_recognition` | 全双工识别 | 云端或本地切全双工，首次唤醒后连续识别 | 配置应用成功；识别态保持；连续 ASR/命令/响应符合需求 | 若播报中识别/打断口径不清，先需求复核，不直接 FAIL。 |
| `online_vad_special_smoke` | 在线 VAD 专项 | 在线状态下播放短句、长停顿、尾弱音等语料 | 在线 ASR/VAD end/云端播报证据；文本覆盖输出 | 截断容忍规则未确认时标记探索性待复核。 |

### 4.4 打断、自播、媒体

| tag | 测试项 | 怎么执行 | 主断言 | 重点归因 |
| --- | --- | --- | --- | --- |
| `interrupt_prerequisite_measurement` | 打断前置自播测量 | 尝试天气、播歌、相声、新闻或离线长播报，选择稳定自播窗口 | 有自播 start/end；时长足够；生成可注入前置 | 候选未识别或时长不足只阻塞打断，不算打断功能失败。 |
| `wake_interrupt` | 自播中唤醒打断 | 在稳定自播窗口内注入唤醒词 | 注入落在自播窗口；有新 wake 或明确打断响应 | 注入点不在窗口内为 `TIMING_AMBIGUOUS`；命中窗口无 wake 才判断设备/固件。 |
| `command_interrupt` | 自播中识别打断 | 在稳定自播窗口内注入命令词 | 注入落在窗口；模式支持时有 ASR/命令证据 | 若模式不允许播报中识别，归需求/配置，不归固件。 |

媒体响应校验目前不仅看“云端有回复”，还要看 AP/upper 日志中的 `audioBroadcast`、TTS URL、player play/stop/complete、HTTP/media error。出现 HTTP recv timeout 但已有媒体播放证据时，倾向先标记 WARN；完全无播放证据才进入媒体链路问题。

### 4.5 联网、误唤醒、归因复核

| tag | 测试项 | 怎么执行 | 主断言 | 重点归因 |
| --- | --- | --- | --- | --- |
| `network_recovery_basic` | 联网恢复基础验证 | 先识别 DUT 联网路径；本机热点可控时关闭/恢复热点，支持 `vir_ssid/vir_pwd` 时改写重启，外部路由可控时走路由器/AP 控制；最后做在线语音 smoke | 网络控制动作成功；设备离线/恢复证据；在线语音 smoke PASS | 热点、外部路由、设备联网、云端语音链路分开归因。 |
| `false_wake_quiet_basic` | 静默误唤醒监听 | 安静环境不播放音频，连续监听 | wake marker 为 0；串口有新日志；无 reboot/crash | 串口无日志=BLOCKED；重启不计入误唤醒；短时 PASS 不代表长期阈值。 |
| `false_wake_human_speech_smoke` | 合成人声干扰误唤醒 | 播放不含唤醒词的人声干扰并监听 | 不应出现 wake marker；串口不断流；无 reboot/crash | 合成语音只是 smoke，正式人声噪还需标准素材/声压/声场。 |
| `false_wake_white_noise_smoke` | 白噪声误唤醒 | 播放白噪声并监听 | 不应出现 wake marker；串口不断流；无 reboot/crash | 正式非人声噪测试仍需噪声素材、音量和 SNR。 |
| `attribution_validator_smoke` | 归因一致性复核 | 二次解析 run 目录、summary 和原始日志 | BDD 结论与模块证据一致；脚本误判能识别 | 原始日志有 marker 但 runner 判 FAIL，要归脚本规则不足。 |

## 5. 已有知识库支持、可继续沉淀的测试项

这些测试项已从旧 `voice-test-plan-designer` 沉淀到 `docs/wiki/voice-validation/`。部分已有 Cucumber tag，部分已有 contract/验证池但还需补资料或环境。

等级说明：

| 等级 | 含义 |
| --- | --- |
| L0 | 已落地执行，可直接用 Cucumber/脚本跑。 |
| L1 | 可立即沉淀，通常不依赖新增物理 rig，数据可从需求抽取或合成。 |
| L2 | 可沉淀但需要词表、oracle、阈值、音频集等资料。 |
| L3 | 可沉淀但需要噪声场、多设备、DOA、低功耗、人工喊测等环境。 |

### 5.1 唤醒类

| 测试项 | 当前能力 | 还需要什么 |
| --- | --- | --- |
| 首次唤醒 | L0，已能做冒烟、压测、统计唤醒率/连续失败/异常重启 | 正式轮次、目标唤醒率、正式音频或音量标定。 |
| 识别模式唤醒 | L0，已能首次唤醒后在识别窗口内二次唤醒 | 识别超时时间、安全边界、正式阈值。 |
| 唤醒相似词 | L2，可生成相似词清单并做不得唤醒断言 | 正式相似词文本/音频、触发率上限。 |
| 唤醒反集 | L2，可导入/生成反集并监听 wake marker | 正式反集、无效样本规则、触发率上限。 |
| 打断唤醒 | L1/L3，已有自播前置发现与窗口注入逻辑 | 是否支持打断、成功标准、自播/人声音量要求。 |
| 响应时间 | L1/L2，已有日志近似统计 | 起点/终点最终定义、阈值、是否需要回采录音。 |
| 连续唤醒 | L1，已有连续调度和稳定性监控 | 轮次、间隔、阈值。 |
| 冷唤醒测试 | L1/L3，可用控制口上下电/静置后测第一轮 | 冷态定义、静置时长、是否允许自动上下电。 |
| 高低音量交替唤醒 | L1/L2，可按播放音量档位调度 | 高/低音量档或 dB、阈值。 |
| 随机间隔唤醒 | L1，已有随机间隔小样本 | 间隔分布、轮次、目标阈值。 |
| 多唤醒词唤醒/切换 | L2，可做分唤醒词统计和串扰矩阵 | 多唤醒词配置入口、音频、唤醒词 ID 日志。 |
| DOA 唤醒 | L3，可写 DOA 日志解析框架 | DOA 支持确认、角度 rig、允许偏差。 |
| 分布式/唯一唤醒 | L3，可写多设备采集和唯一应答率框架 | 多台设备、组网规则、摆位。 |
| 低功耗唤醒 | L3，可写进入/退出和恢复耗时框架 | 低功耗进入方式、设备支持、恢复标准。 |
| 变换位置/不同人群唤醒 | L2/L3，可按标签统计 | 带标签音频、人工喊测记录、场景矩阵。 |

### 5.2 命令词类

| 测试项 | 当前能力 | 还需要什么 |
| --- | --- | --- |
| 基础命令词识别率 | L0/L1，已能读命令文件，先唤醒再识别 | 期望动作/意图 oracle、正式阈值。 |
| 全命令覆盖 | L0/L1，可从需求抽取命令全集并检查覆盖 | 产品应覆盖全集的权威来源。 |
| 命令词反集/集外误识别 | L2，可导入/生成候选并断言不得触发正式命令 | 正式反集/集外语料、误触发上限。 |
| 短词/长词/数字类指令 | L1/L2，可按长度/数字参数分组统计 | 分类规则、数字参数有效范围。 |
| 噪声误识别 | L2/L3，可写噪声监听和误触发断言 | 噪声素材、SNR、音量、时长、阈值。 |
| 自激 | L1/L3，可用长自播内容监控是否回灌识别 | 自激判定标准、需要的自播内容。 |
| 识别打断 | L1/L3，已有自播窗口内注入命令逻辑 | 打断成功定义、插入时机、目标命令。 |
| 离线 oneshot | L1/L2，已有间隔矩阵 | 设备支持确认、正式 one-shot 音频或允许合成。 |
| 停顿/连读/语速 | L1/L2，可生成变体并分组统计 | 是否需要真实音频变速、阈值。 |
| 角度/距离/姿态变化 | L3，可参数化记录 | 距离/角度/姿态 rig。 |
| 命令响应时间 | L1/L2，可从日志估计命令结束到反馈时间 | 终点选择、阈值、是否需要录音标注。 |

### 5.3 自由说类

| 测试项 | 当前能力 | 还需要什么 |
| --- | --- | --- |
| 全意图覆盖 | L1/L2，可从需求抽取意图/slot/说法 | 需求文档中意图/slot 是否为准。 |
| 高频/非高频说法识别率 | L1/L2，可抽取或生成候选 | 高频标记、长尾是否允许合成、阈值。 |
| 纯 slot 说法 | L1/L2，可按 slot 表组合 | slot 组合规则、目标意图。 |
| 全量覆盖率测试 | L1/L2，可做批跑框架和低识别意图统计 | 全量表来源、最低样本数。 |
| 前缀/后缀/插入语 | L1，可自动生成“帮我/请/一下/吧”等变体 | 允许插入语集合、是否人工复核。 |
| 口语化命令表达 | L1/L2，可从标准说法生成自然表达 | 哪些表达算可接受。 |
| 自由说反集 | L2，可导入/生成候选并断言不得命中正式意图 | 正式反集、负向边界、阈值。 |
| 口音/语速/人群 | L2/L3，可按标签统计 | 带标签音频或人工喊测。 |
| 主观自由发挥 | L3，可记录真实表达和目标功能 | 人工体验者、可接受标准。 |
| 自由说响应时间 | L1/L2，可做端到端耗时统计 | 终点定义、阈值。 |

### 5.4 在线识别、联网、云端类

| 测试项 | 当前能力 | 还需要什么 |
| --- | --- | --- |
| 半双工识别 | L0/L1，已有 tag 和配置/识别闭环 | 项目切换入口、语料、阈值。 |
| 全双工识别 | L0/L1，已有 tag 和连续识别口径 | 全双工配置、退出识别态 marker。 |
| 方言/口音在线识别 | L2/L3，可分标签 ASR 统计 | 方言/口音音频、期望文本。 |
| 在线噪声误识别 | L2/L3，可写误触发断言 | 噪声素材、阈值。 |
| 全双工识别打断 | L1/L3，可用自播前置 + 在线语料注入 | 打断标准、插入时机。 |
| 在线 one-shot | L1/L2，已有间隔矩阵 | 期望 ASR、半/全双工是否都测。 |
| VAD 专项 | L1/L2，已有 tag 和候选分类 | 期望文本、截断容忍规则。 |
| 弱网稳定性 | L1/L3，已有热点断开/恢复基础链路 | 严格弱网参数：带宽、延迟、丢包。 |
| 云端服务稳定性 | L1/L2，可多时段重复跑和统计波动 | 测试时段、服务异常口径。 |
| 在线响应时间 | L1/L2，可做端到端耗时 | 起止点定义、阈值。 |
| 音量/夜间模式/Mic/多唤醒/阈值/唤醒音频上传/主动交互 | L1，已有云控动作和验证池 | API 环境、设备端 UAT/SIT、读回 marker、持久化要求。 |

### 5.5 误唤醒与长期稳定性

| 测试项 | 当前能力 | 还需要什么 |
| --- | --- | --- |
| 安静误唤醒 | L1/L3，已有静默监听 tag | 安静环境、测试时长、频度上限。 |
| 人声噪误唤醒 | L2/L3，已有合成人声 smoke | 标准人声噪素材、音量、声场。 |
| 非人声噪误唤醒 | L2/L3，已有白噪声 smoke | 标准非人声噪素材、SNR、音量。 |
| 多点噪误唤醒 | L3，可写多声源框架 | 多音箱/多点 rig、噪声组合。 |
| 高/低灵敏度误唤醒 | L2/L3，可分灵敏度统计 | 灵敏度设置入口、档位、每档时长。 |
| 整机长期挂机 | L1/L3，可长时监控唤醒、重启、日志中断 | 挂机时长、环境、异常处理策略。 |
| 在线混合交互压测 | L1，已有 WB01/WS63 类压力脚本方法 | 场景权重、结束时间、网络环境、媒体校验口径。 |

## 6. 断言体系

### 6.1 证据源职责

| 证据源 | 能证明什么 | 不能单独证明什么 |
| --- | --- | --- |
| CP/cskcp | 底层唤醒、离线识别、命令/tone、部分算法日志 | 云端业务是否完成。 |
| AP/cskap | 主控、deviceinfo、TTS、配置、云控回调、媒体播放、业务执行 | CP 底层是否一定唤醒。 |
| ASR/upper | 在线/离线 ASR、网络状态、云端语音链路 | 业务动作是否一定执行。 |
| control 串口 | 上电/掉电/PA 命令是否发出 | 业务功能是否成功。 |
| 播放脚本 | PC 是否向目标声卡输出音频 | DUT 是否真正听到。 |
| 云端 HTTP | 云端是否接受请求 | 设备是否收到并应用。 |
| 热点状态 | PC 热点或网络承载是否变化 | 设备端业务是否在线可用。 |

### 6.2 统一结果口径

| 结果 | 判定条件 |
| --- | --- |
| PASS | 前置满足、输入真实发生、设备收到或识别到输入、业务状态/动作/反馈符合需求、无禁止行为和异常重启。 |
| FAIL | 环境有效、采集有效、需求明确支持、断言正确，实测行为仍与需求矛盾。 |
| BLOCKED | 串口/声卡/网络/token/设备身份/控制口等前置不可用，无法判断功能。 |
| NEEDS_REVIEW | 需求不清、oracle 缺失、机型是否支持不明确、文档矛盾。 |
| TIMING_AMBIGUOUS | 注入点、超时边界、播报窗口等时序证据不够，不能判固件。 |
| WARN | 功能主链路通过但存在媒体 retry、偶发无 ASR、弱证据等风险。 |

### 6.3 常用断言项

| 断言项 | PASS 例子 | FAIL/BLOCKED 区分 |
| --- | --- | --- |
| 播放成功 | `playback_returncode == 0` | 声卡不存在/播放失败为环境 BLOCKED；播放成功不等于 DUT 听到。 |
| 串口可用 | 端口打开且窗口内有新日志 | 端口打不开/日志全空为 BLOCKED。 |
| 唤醒成功 | CP/AP/ASR 或 AP/upper 出现 wake marker | 无任何 wake：先查声卡、PA、麦克风链路；部分端口缺失按模块定位。 |
| 识别模式 | 首次唤醒后未超时，二次唤醒/命令仍进入识别 | 二次播放超过超时为测试问题；临界点标记时序风险。 |
| 命令识别 | 有 ASR 文本、keyword、命令 ID、tone、动作或 TTS 闭环 | 有唤醒无 ASR 是 ASR/固件；ASR 文本与期望不一致可能是 oracle/词表问题。 |
| 误识别记录 | 窗口内实际 ASR 文本、CP `WAKE(0)`、AP algo keyword 都被记录 | 没说某词却识别出该词，按误识别/串音/上轮残留/词表 oracle 复核；不能只因为有 ASR 就判 PASS。 |
| 在线响应 | online_asr、cloud reply、audioBroadcast、TTS/media play | 设备未在线为前置；云端成功但无设备日志不能判 PASS。 |
| 媒体播放 | 有 TTS URL、soundplayer/ttsplayer play/stop/complete | HTTP timeout 但已播放可 WARN；完全无播放证据才 FAIL。 |
| 云控设置 | HTTP 成功 + 设备收到 + 状态读回/行为变化 + 必要时重启持久化 | 只有 HTTP 成功不算 PASS；云端返回不支持为需求/能力问题。 |
| 联网恢复 | 可控网络动作 + 设备 offline/online marker + 在线语音 smoke | 先判 DUT 是否连接本机热点；外部 Wi-Fi 需要路由器/AP 控制或 `vir_ssid/vir_pwd` 改写重启，否则 `BLOCKED_NETWORK_NOT_CONTROLLABLE`。 |
| 误唤醒 | 监听窗口 wake/ASR/command marker 都为 0，串口不断流 | 环境噪声/设备重启/串口中断不能直接算误唤醒；静默或干扰窗口出现任意识别都要保留原始行。 |
| 重启/崩溃 | 无 Boot Reason/watchdog/assert/panic/fatal/reboot 等异常 marker | `player reset by user`、`ignore exception` 等播放器/超时日志不能误判重启。 |

### 6.4 临界超时处理

识别模式唤醒、半/全双工、one-shot 和打断都容易卡在临界点。统一处理：

- 不把注入点安排在超时边界正负几百毫秒内。
- 配置 `timing_guard_ms`，建议至少 1000 到 1500ms。
- 唤醒播报还没结束时，不直接开始需要精确定义的下一步，除非该用例就是验证播报中打断。
- 每轮记录：播放开始、播放结束、wake marker、ASR marker、TTS/media start/end。
- 同时记录本轮“实际识别到什么”：`recognized_texts`、`recognized_commands`、`wake_keywords`；额外识别结果可能就是误唤醒/误识别证据。
- 如果“看起来刚好超时”但日志无法证明，结果标为 `TIMING_AMBIGUOUS`，重跑带更大 guard 的最小用例。

## 7. 怎么写 Cucumber 用例

### 7.1 Feature 写法

已注册功能可以这样写，重点是 tag 对上：

```gherkin
# language: zh-CN
@polaris @voice-core @first_wake
功能: 唤醒冒烟

  背景:
    假如 使用当前 Polaris 本地串口配置
    而且 使用默认播放声卡
    而且 所有调试输出写入 Cucumber 调试目录

  场景: 首次唤醒
    假如 设备处于待唤醒状态
    当 播放唤醒词
    那么 应观察到 CP/AP/ASR 唤醒证据
    而且 未唤醒、播放失败或串口日志缺失应被区分归因
```

命令词用例：

```gherkin
@P0 @command @basic_command_recognition
场景: 基础命令词识别
  假如 已准备命令词词表和固定唤醒词
  当 对每条命令词执行先唤醒再识别
  那么 每条命令词应观察到唤醒、ASR 和命令关键词证据
  而且 FAIL 需要区分命令未播、未唤醒、未识别、词表期望不一致和设备行为问题
```

打断用例：

```gherkin
@P1 @interrupt @wake_interrupt
场景: 自播中唤醒打断
  假如 已有可用的自播打断前置和安全注入点
  当 在设备自播窗口内播放唤醒词
  那么 应观察到新的唤醒证据或明确的打断响应
  而且 注入未落入自播窗口时应标记为时序不明确而不是固件失败
```

### 7.2 Task JSON 写法

最小任务：

```json
{
  "schema": "polaris.cucumber.task.v1",
  "task_id": "first_wake_smoke",
  "scenario": { "tag": "first_wake" },
  "runner": { "mode": "dry-run" },
  "environment": { "env_file": "polaris.local.json" },
  "execution": {
    "observe_ms": 15000,
    "manage_session": true,
    "allow_side_effects": false
  }
}
```

命令词任务：

```json
{
  "schema": "polaris.cucumber.task.v1",
  "task_id": "basic_command_20",
  "scenario": { "tag": "basic_command_recognition" },
  "runner": { "mode": "dry-run" },
  "environment": { "env_file": "polaris.local.json" },
  "inputs": {
    "command_file": "docs/fa2命令词.txt",
    "command_limit": 20
  },
  "execution": {
    "observe_ms": 15000,
    "manage_session": true,
    "allow_side_effects": false
  }
}
```

真机执行时可以命令行覆盖：

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py --task path\to\task.json --mode execute --allow-side-effects --manage-session
```

### 7.3 用例变化时脚本要不要变

| 用例变化 | 是否要改脚本/registry |
| --- | --- |
| 同一个 tag，换命令词文件、轮次、观察窗口 | 不需要，改 task JSON。 |
| 同一个功能，换唤醒词、串口、声卡、Wi-Fi | 不需要，改 `polaris.local.json`。 |
| 同一个功能，feature 文案轻微调整但步骤语义不变 | 通常不需要。 |
| 新增一个已存在动作的组合场景 | 可能只需要加 feature/tag 和 mapping。 |
| 新动作，例如新的云控 API、新控制口命令 | 需要加 `action_registry.json` 和执行脚本/参数。 |
| 新断言，例如新增媒体首包耗时或 DOA 角度偏差 | 需要加 `assertion_registry.json`、summary 解析和归因。 |
| 新功能意图，例如低功耗唤醒、分布式唯一唤醒 | 需要新增 contract、feature、mapping、registry、task 示例。 |

这就是为什么“任意新写的自然语言 Cucumber 用例”不能直接脱离大模型执行：脚本只能执行已注册的动作和断言。注册一次后，后续就不需要大模型和网络来改执行脚本。

## 8. 新测试项沉淀流程

1. 读需求/表格/方案，提取测试项和数据来源。
2. 用 `references/validation-pool/INDEX.md` 找模块，例如唤醒读 `wake-session.md`，双工读 `duplex-mode.md`。
3. 拆成“功能意图、前置、动作、输入、证据、断言、禁止行为、归因”。
4. 如果已有 tag，就只补 task JSON 和输入数据。
5. 如果没有 tag，先在 `features/polaris_voice_core.feature` 新增场景。
6. 在 `references/voice_core_mapping.json` 写执行命令、默认参数、assertions、failure_split。
7. 必要时补 `step_registry.json`、`action_registry.json`、`assertion_registry.json`。
8. 新增 `tasks/examples/*.json` 或项目任务文件。
9. 先跑 `dry-run`，看 `execution_plan.md` 是否正确。
10. 真机小样本跑 1 到 5 轮，确认日志 marker 与断言对齐。
11. 修复脚本误判和时序灰区。
12. 再做大样本或压测，结果进入 debug/result，不提交运行日志。
13. 把通用规则回灌到 `references/validation-pool/` 或本文件。

## 9. 失败归因速查

| 现象 | 优先归因 | 下一步 |
| --- | --- | --- |
| 播放命令失败、找不到 device key | 声卡/环境 | `laid` 查声卡；声卡未配置时走默认声卡；确认 Windows 默认输出。 |
| 播放成功但三端完全无 wake | 音频链路/PA/麦克风/设备状态 | 控制口执行 `uut-pa.on`、`pa-enable.set 0 17 0 1`，复播唤醒。 |
| 只有 CP 有 wake，AP/ASR 没有 | AP/链路/状态机 | 查 AP wakeup_callback、配置状态、是否 mic 关闭。 |
| AP 有 wake，ASR/upper 无 ASR | ASR/上位/在线链路 | 查 ASR cmd、online_asr、网络状态。 |
| 云控 HTTP 成功但设备无变化 | 设备环境/API 环境/connector/固件应用 | 确认 UAT/SIT、deviceinfo、设备收到配置、读回状态。 |
| 在线问答有云端 reply 但没有播放 | 媒体/TTS 链路 | 查 audioBroadcast、TTS URL、player play/stop/complete、HTTP error。 |
| 自播打断失败但注入点不清楚 | 测试时序 | 重新测自播窗口，扩大 guard，标记 `TIMING_AMBIGUOUS`。 |
| 随机压测出现疑似重启 | 先判脚本/日志 marker | 查 Boot Reason/watchdog/assert/panic/fatal，过滤 player reset/ignore exception。 |
| 词表期望和识别文本不一致 | 需求/oracle/语义映射 | 不直接判固件，先核对需求词表和可接受说法。 |
| 串口无日志或 logger 断开 | 测试环境 | BLOCKED，不能判功能失败。 |
| 设备不支持该能力或云端返回不支持 | 需求/能力矩阵 | 标 `NEEDS_REVIEW` 或不适用，不写成 PASS/FAIL。 |

## 10. 交付与维护规则

- 稳定配置入口只放在 `polaris.local.json`，不要让新人去改 `config/` 的历史状态文件。
- 运行结果、串口日志、压测日志默认放 `satellite/cucumber-agent-testing/debug/` 或 `result/`，不提交 git。
- 新增功能必须同步：feature、mapping、registry、task 示例、文档说明。
- 修改断言后必须用旧 run 目录或小样本复核，避免把脚本问题伪装成固件问题。
- 需求不清时输出 `NEEDS_REVIEW`，不要为了凑结论把问题写成固件 FAIL。
- 每次实际执行和状态变化按 `AGENTS.md` 同步 `plan.md`。
