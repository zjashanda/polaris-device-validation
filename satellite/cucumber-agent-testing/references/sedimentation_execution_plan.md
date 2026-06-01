# Polaris Cucumber 沉淀执行计划

本文件用于回答“为什么停了”之后的继续执行记录：先把可离线固化的能力写成可审阅的 registry 设计，再等待命令执行环境恢复后从 `docs/requirements/` 自动抽取语料并执行验证。

## 当前策略

1. 不等待用户再补资料，优先使用现有资料：
   - `docs/requirements/` 中的需求文档。
   - `docs/fa2命令词.txt`。
   - `satellite/voice-test-plan-designer/` 的测试项知识库。
   - 当前本地串口、声卡、热点、COM15 配置。
2. 用户资料有限时，允许先生成候选语料：
   - 前缀/后缀/插入语自由说候选。
   - 口语化表达候选。
   - oneshot 间隔任务候选。
   - 命令词短/长/数字分组候选。
3. 候选语料只用于摸底和沉淀流程，不把未经确认的负向候选直接作为正式 FAIL 依据。
4. 打断类测试优先自发现前置：
   - 在线查天气/播歌/长 TTS。
   - 离线命令播报时长扫描，选择最长播报命令作为打断前置。

## 第一批可直接写入 registry 的能力

| 能力 ID | 覆盖测试项 | 动作沉淀 | 断言沉淀 |
| --- | --- | --- | --- |
| `wake.first` | 首次唤醒 | 等待待唤醒、播放唤醒词、采集 CP/AP/ASR | CP/AP/ASR 唤醒闭环、播放成功、无设备异常 |
| `wake.recognition_mode` | 识别模式唤醒 | 首次唤醒、识别窗口内再次播放唤醒词 | 识别态内再次唤醒成功，临界超时灰区单独标记 |
| `wake.continuous` | 连续唤醒 | 连续或短间隔播放唤醒词 | 专项唤醒率、连续失败段、日志中断/重启 |
| `wake.random_interval` | 随机间隔唤醒 | 随机 1~60s 间隔播放唤醒词 | 随机间隔唤醒率、状态异常 |
| `wake.cold` | 冷唤醒 | 静置或 COM15 上下电后第一轮唤醒 | 冷态第一轮唤醒成功、恢复识别 |
| `wake.latency` | 唤醒响应时间 | 播放唤醒词并记录日志/播放时间轴 | 成功样本耗时、最大/最小/平均、超阈值 |
| `command.basic` | 基础命令词识别率 | 唤醒后播放命令词 | 识别正确、拒识、集内串扰、动作/TTS |
| `command.coverage` | 全命令覆盖 | 抽取需求命令全集并比对测试集 | 命令覆盖率、缺失命令、样本不足 |
| `command.grouped` | 短词/长词/数字类 | 自动按文本长度/数字参数分组 | 分组识别率、分组串扰率 |
| `command.oneshot_offline` | 离线 oneshot | 唤醒词+短间隔+命令词 | 唤醒成功且命令识别正确，失败环节拆分 |
| `command.interrupt` | 识别打断 | 自播中插入第二命令 | 自播被打断、新命令识别正确 |
| `command.self_excitation` | 自激 | 触发设备自播并监听回灌 | 自播不应触发命令或重复执行 |
| `free.coverage` | 全意图覆盖/全量覆盖 | 从需求抽取意图/slot/说法 | 意图覆盖率、slot 覆盖率、缺失项 |
| `free.paraphrase` | 前缀/后缀/插入语/口语化 | 自动生成候选表达 | 目标意图命中或标记待人工复核 |
| `online.half_duplex` | 半双工识别 | 每条在线语料前唤醒 | ASR 文本正确、识别为空、超时 |
| `online.full_duplex` | 全双工识别 | 首次唤醒后连续在线语料 | 识别态保持、ASR 句准、退出后重唤醒 |
| `online.oneshot` | 在线 oneshot | 唤醒词+短间隔+在线语料 | 唤醒成功且在线 ASR 正确，失败环节拆分 |
| `online.interrupt` | 全双工识别打断 | 自播中插入在线语料 | 打断场景 ASR 正确、无超时 |
| `online.vad` | VAD 专项 | 短/长/尾弱音/停顿语料 | 截断率、超时率、句准 |
| `network.recovery` | 弱网/联网恢复 | 热点开关、设备接入、云端状态检查 | 在线状态、恢复耗时、网络异常归因 |
| `false_wake.quiet` | 安静误唤醒 | 长时间监听 | 误唤醒次数/小时、触发日志 |
| `false_wake.noise` | 人声/非人声噪误唤醒 | 长播噪声素材 | 触发次数、触发唤醒词、听音复核占位 |
| `attribution.validator` | 全部测试项 | 二次解析原始日志 | 脚本误判/设备环境/固件算法/需求不清归因 |

## 第二批等待资料或环境的能力

| 能力 ID | 需要资料/环境 |
| --- | --- |
| `wake.similar` | 正式相似词文本/音频、触发率上限 |
| `wake.negative` | 正式唤醒反集、无效样本规则 |
| `command.negative` | 命令词反集/集外语料、负向边界 |
| `noise.recognition` | 噪声素材、播放音量/SNR、误触发阈值 |
| `free.negative` | 自由说反集、不得命中意图 |
| `doa.wake` | DOA 支持、角度 rig、允许偏差 |
| `distributed.wake` | 多设备、主设备规则、摆位 |
| `low_power.wake` | 低功耗入口、恢复判定 |
| `position.people` | 距离/角度/人群音频或喊测记录 |
| `sensitivity.false_wake` | 灵敏度设置入口、档位定义 |

## 命令执行环境恢复后的动作

1. 执行 `python satellite\cucumber-agent-testing\scripts\ingest_requirements_corpus.py`。
2. 审查输出：
   - `debug/requirements_corpus/<stamp>/corpus_candidates.csv`
   - `debug/requirements_corpus/<stamp>/synthetic_variants.csv`
3. 生成第一批 registry 草案：
   - `references/generated_requirement_oracle.json`
   - `references/generated_synthetic_utterances.json`
   - `references/generated_interrupt_prerequisites.json`
4. 执行小规模 smoke：
   - 命令词抽样。
   - 自由说候选抽样。
   - 在线半/全双工各 1 条。
   - 打断前置自发现。
5. 将 PASS/BLOCKED/需复核项同步到报告。

