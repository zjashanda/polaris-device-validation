# 自动化测试优化方案在 Polaris Skill 的落地方案

来源：`C:\Users\Administrator\Desktop\自动化测试优化方案.pdf`

本文不是重做一套大平台，而是在当前 Polaris skill、Cucumber Agent Testing、validation-pool、串口/声卡/云控脚本基础上，把 PDF 里的核心能力按“轻平台、本地优先、CLI 可复用”的方式落地。

## 1. PDF 核心思想提炼

PDF 目标很明确：

- 用例失败可复现。
- 失败原因可分析。
- 相似失败可聚类。
- 失败场景可回归。
- 自动化结果形成长期可复用资产。

核心新增三件事：

1. 数据采集：执行前状态、执行后状态、日志、版本、识别结果、状态差异都要结构化保存。
2. 策略模块化：场景生成策略、重试策略、失败分析策略、高危标记策略都做成可编辑策略池，不写死在某个测试工具里。
3. 结果分析：按用例、场景、版本、设备、状态变化、日志指纹聚类，生成可回归的失败场景。

PDF 的层次可以映射为：

| PDF 层级 | 在 Polaris 中的落地位置 |
| --- | --- |
| 测试控制层 | 新增 optimized runner：执行顺序、重试、场景策略、生命周期。 |
| 用例执行层 | 复用当前 Cucumber runner、phrase probe、fa2 batch、云控、网络、上下电脚本。 |
| 数据采集与存储层 | 新增统一 execution/scene/failure 数据模型，保存状态快照、diff、日志索引。 |
| 分析生成与回归层 | 新增分析器、失败指纹、聚类、回归任务生成器。 |

## 2. 当前 skill 已经具备的基础

当前不需要从零开始，已有这些能力可以直接复用：

| 已有能力 | 对应 PDF 能力 |
| --- | --- |
| `polaris.local.json` 项目化串口/声卡/Wi-Fi/云环境配置 | 本机设备配置。 |
| `satellite/cucumber-agent-testing/scripts/run_task.py` | 单任务执行入口。 |
| `features/polaris_voice_core.feature` + registry/mapping | 用例意图、动作、断言固化。 |
| `run_cucumber.py` 和各类模块 runner | 用例执行层。 |
| `tools/probe/polaris_state_probe.py`、doc runner 中的 state diff | 执行前/后状态快照与状态差异。 |
| `references/evidence-rules.md` | PASS/FAIL/BLOCKED/需求问题/固件问题归因规则。 |
| `references/validation-pool/` | 功能验证逻辑和策略池雏形。 |
| `evidence_validator.py`、`run_attribution_validator.py` | 结果复核与脚本误判识别。 |
| WB01/WS63 在线压测脚本经验 | 随机场景、长稳、媒体校验、重启误判修正经验。 |

当前主要缺口：

- 还没有统一的 `execution_id / retry_index / scene_id` 数据模型。
- Cucumber 单用例可以跑，但“执行前状态 -> 执行 -> 执行后状态 -> 自动重试 -> 稳定性分类”还不是统一控制层。
- 随机场景压测目前偏项目脚本，还没有通用场景生成策略池。
- 失败结果有报告，但还缺少跨 run 的失败聚类、失败指纹和回归任务生成。
- 设备健康度目前靠人工读 summary，还没有统一评分和标签。

## 3. 推荐落地原则

### 3.1 不上来做大平台

先做“本地轻平台”：

- 所有入口仍是 Python CLI。
- 当前只保留本地 CLI；外部调度暂不纳入当前 skill。
- 数据先用 JSON/JSONL/Markdown 落盘，后续需要再加 SQLite。
- 不引入 Web 服务、不引入数据库服务、不改变当前真机调试路径。

### 3.2 保留 Cucumber 作为用例语义层

Cucumber 不负责智能分析，只负责：

- 描述功能意图。
- 绑定 tag。
- 通过 mapping/registry 找到执行逻辑。

新增优化能力放在 Cucumber 外层：

```text
Optimized Runner
  -> 读取 task/scene/strategy
  -> 执行前采集状态
  -> 调 Cucumber run_task/run_cucumber
  -> 执行后采集状态
  -> 计算 state_diff / evidence / failure_signature
  -> 按 retry_policy 重试
  -> 写 execution_record / scene_record
  -> 触发分析与回归任务生成
```

### 3.3 策略池可编辑，不写死

这些内容都应该放配置文件：

- 哪些 tag 权重高。
- 哪些失败需要重试。
- 每类失败重试几次。
- 随机场景怎么组合。
- 高危用例如何标记。
- 哪些状态字段必须恢复。
- 哪些日志 marker 属于重启、crash、媒体异常、网络异常。

## 4. 建议目录结构

建议新增这些稳定文件，运行结果仍放 debug/result，不提交日志。

```text
satellite/cucumber-agent-testing/
  references/
    optimization/
      execution_record.schema.json       # 单次执行数据模型
      scene_record.schema.json           # 场景执行数据模型
      retry_policy.json                  # 重试与稳定性分类策略
      scene_strategy_pool.json           # 随机/权重/扰动场景策略
      failure_signature_rules.json       # 失败指纹提取规则
      health_metrics.json                # 设备健康度指标定义
      state_restore_policy.json          # 用例污染和状态恢复规则
  scripts/
    run_optimized_task.py                # 单用例：状态采集 + 执行 + 重试 + 记录
    generate_scene.py                    # 根据策略生成 scene.plan.json
    run_scene.py                         # 顺序执行场景中的多个 Cucumber task
    analyze_execution_store.py           # 聚类、统计、健康度、失败模式
    build_regression_task.py             # 从失败场景生成回归 task/scene
  tasks/
    scenes/
      online_mixed_stress.example.json   # 场景任务示例
      wake_asr_network_perturb.example.json
```

输出目录建议：

```text
satellite/cucumber-agent-testing/debug/optimized_runs/<日期>/<execution_id>/
satellite/cucumber-agent-testing/debug/scenes/<日期>/<scene_id>/
satellite/cucumber-agent-testing/debug/analysis/<日期>/
```

## 5. 数据模型落地

### 5.1 单用例 execution_record

每次执行一条 Cucumber 场景，都生成一个结构化记录：

```json
{
  "schema": "polaris.execution_record.v1",
  "project": "cskwb01",
  "case_id": "first_wake_smoke",
  "scenario_tag": "first_wake",
  "execution_id": "EXEC_20260525_153000_001",
  "retry_index": 0,
  "seed": 12345,
  "started_at": "2026-05-25T15:30:00+08:00",
  "ended_at": "2026-05-25T15:30:20+08:00",
  "result": "FAIL_NO_WAKE",
  "normalized_result": "FAIL",
  "fail_type": "STABLE_FAIL",
  "failure_owner": "audio_or_device_hearing_chain",
  "asr_result": [],
  "device_state_before": "state/before.json",
  "device_state_after": "state/after.json",
  "state_diff": "state/state_diff.json",
  "firmware_version": "...",
  "algorithm_version": "...",
  "logs": {
    "ap": "logs/COM14_ap.window.log",
    "cp": "logs/COM13_cp.window.log",
    "asr": "logs/COM12_asr.window.log",
    "tool": "tool.log"
  },
  "evidence_summary": "evidence_summary.json",
  "failure_signature": "no_wake|playback_ok|serial_ok|state_idle",
  "pollution": {
    "detected": false,
    "dirty_fields": []
  }
}
```

最小必做字段：

- `case_id`
- `execution_id`
- `retry_index`
- `scenario_tag`
- `result`
- `device_state_before`
- `device_state_after`
- `state_diff`
- `logs`
- `failure_signature`

### 5.2 场景 scene_record

场景是多个用例的有序组合：

```json
{
  "schema": "polaris.scene_record.v1",
  "project": "cskwb01",
  "scene_id": "SCENE_20260525_153000_001",
  "strategy_id": "weighted_online_mixed_v1",
  "case_sequence": [
    { "case_id": "first_wake", "scenario_tag": "first_wake" },
    { "case_id": "music", "scenario_tag": "online_media_interaction" },
    { "case_id": "network_recovery", "scenario_tag": "network_recovery_basic" },
    { "case_id": "basic_command", "scenario_tag": "basic_command_recognition" }
  ],
  "device_initial_state": "state/scene_before.json",
  "scene_events": "scene_events.jsonl",
  "executions": ["EXEC_001", "EXEC_002", "EXEC_003"],
  "fail_case": "EXEC_003",
  "firmware_version": "...",
  "result": "FAIL",
  "failure_signature": "network_recovered_but_online_asr_missing"
}
```

## 6. 重试与稳定性分类

PDF 里提到：

- 3 次 retry 全失败 -> `STABLE`
- 3 次 1 过 2 挂 -> `FLAKY`
- 环境相关 -> `ENV_RELATED`

建议沉淀为 `retry_policy.json`：

```json
{
  "schema": "polaris.retry_policy.v1",
  "default": {
    "max_retries": 2,
    "retry_wait_s": 3,
    "retry_on": ["FAIL_NO_WAKE", "WARN_NO_ASR", "WARN_MEDIA_ERROR", "FAIL_NETWORK_RECOVERY"],
    "no_retry_on": ["BLOCKED_SERIAL", "BLOCKED_AUDIO_DEVICE", "NEEDS_REVIEW", "TIMING_AMBIGUOUS"]
  },
  "classification": {
    "all_fail": "STABLE_FAIL",
    "pass_after_retry": "FLAKY_FAIL",
    "blocked_by_environment": "ENV_RELATED"
  }
}
```

落地规则：

- 单轮失败后先看是否属于可重试类型。
- 重试前重新采集状态，必要时执行状态恢复。
- 每次 retry 都有独立 `execution_id`，但归属同一个 `case_id`。
- 最终给出 `final_result` 和 `fail_type`。
- 如果重试后 PASS，原始失败仍保留，标为 `FLAKY_FAIL`，用于后续聚类。

## 7. 随机场景与策略池

### 7.1 场景生成类型

先支持三类：

| 策略 | 用途 | 例子 |
| --- | --- | --- |
| 普通随机 | 发现偶发问题 | 从可执行 tag 中随机抽 N 个。 |
| 权重随机 | 保证重点场景覆盖 | basic_command x4、music x3、news x3、qa x2。 |
| 指定扰动 | 找顺序依赖和状态污染 | `wakeup -> asr -> network_disconnect -> asr`。 |

### 7.2 scene_strategy_pool 示例

```json
{
  "schema": "polaris.scene_strategy_pool.v1",
  "strategies": {
    "weighted_online_mixed_v1": {
      "description": "在线基础命令、音乐、相声、新闻、问答混合压测",
      "mode": "weighted_random_bag",
      "bag": [
        { "scenario_tag": "basic_command_recognition", "weight": 4 },
        { "scenario_tag": "online_music_interaction", "weight": 3 },
        { "scenario_tag": "online_crosstalk_interaction", "weight": 3 },
        { "scenario_tag": "online_news_interaction", "weight": 3 },
        { "scenario_tag": "online_qa_interaction", "weight": 2 },
        { "scenario_tag": "network_recovery_basic", "weight": 1 }
      ],
      "state_restore_required": true,
      "max_cases_per_scene": 16
    },
    "wake_asr_network_perturb_v1": {
      "mode": "fixed_sequence",
      "sequence": [
        "first_wake",
        "basic_command_recognition",
        "network_recovery_basic",
        "online_oneshot_matrix"
      ]
    }
  }
}
```

说明：当前仓库已经有在线混合压测经验，但它还偏项目脚本。落地后应该把“权重袋”和“场景组合”抽成策略池，runner 只解释策略。

## 8. 状态采集、状态污染和恢复

### 8.1 执行前后必须采集

每条用例至少采集：

- 设备在线状态。
- AP/CP/ASR/upper 日志新鲜度。
- wakeup_id。
- duplex 模式。
- volume。
- mic 状态。
- night mode。
- network/cloud status。
- 最近 boot reason。
- 媒体播放状态。
- 固件/算法版本。

能从 `deviceinfo` 或串口命令取到的就取；取不到就写 `unknown`，不要伪造。

### 8.2 状态污染判定

用例结束后如果必须恢复默认状态但没有恢复，要标记“用例污染”：

```json
{
  "schema": "polaris.state_restore_policy.v1",
  "default_expected_state": {
    "network": "on",
    "duplex_mode": "project_default",
    "volume": "project_default",
    "mic": "on",
    "night_mode": "off"
  },
  "dirty_state_result": "CASE_POLLUTION",
  "auto_restore": true
}
```

例子：

- Case A 关了 mic，但结束后没有开回去。
- 后续 Case B 无法唤醒，不应直接判 B 的固件失败。
- runner 应把 B 标为“被前置状态污染”，并把 A 标为污染源候选。

## 9. 失败指纹与聚类

### 9.1 指纹组成

PDF 里提到：`error_code + key_stack + module`，Polaris 可以扩展为：

```text
failure_signature = normalized_result
                  + module
                  + key_log_marker
                  + state_diff_summary
                  + scenario_context
                  + firmware_version
                  + algorithm_version
```

示例：

| 失败 | 指纹 |
| --- | --- |
| 播放成功但无唤醒 | `FAIL_NO_WAKE|playback_ok|serial_ok|no_wake_marker|mic_unknown|pa_unknown` |
| 在线媒体 WARN | `WARN_MEDIA_ERROR|audioBroadcast_seen|player_play_seen|http_recv_timeout` |
| 脚本误判重启 | `SCRIPT_FALSE_POSITIVE|ignore_exception|no_boot_reason|no_watchdog` |
| 云控不生效 | `CLOUD_APPLY_NO_EFFECT|http_200|device_env_mismatch|no_readback_change` |

### 9.2 聚类输出

`analyze_execution_store.py` 输出：

- `failure_clusters.json`
- `failure_clusters.md`
- `high_risk_cases.md`
- `flaky_cases.md`
- `device_health_report.md`
- `regression_candidates.json`

聚类维度：

- 用例维度：高失败率用例。
- 固件/算法版本：新引入或回归。
- 设备型号：设备相关性。
- 状态变化：网络、模式、音量、mic、night mode。
- 场景序列：顺序依赖和状态污染。
- 日志特征：boot reason、watchdog、panic、HTTP timeout、no wake、no ASR。

## 10. 设备健康度

先不追求 CPU/MEM 全量，因为不一定每个项目日志都有。第一阶段先做可获得指标：

| 指标 | 来源 | 用途 |
| --- | --- | --- |
| 连续运行时长 | runner start/end、日志心跳 | 判断长稳能力。 |
| 重启次数 | Boot Reason、reboot/watchdog/panic/assert marker | 判断稳定性。 |
| 串口断流次数 | logger heartbeat、log freshness | 判断采集/设备异常。 |
| 唤醒失败率 | wake 场景 summary | 判断音频/唤醒稳定性。 |
| ASR 空结果率 | ASR/upper 日志 | 判断识别稳定性。 |
| 媒体 WARN 率 | AP media/TTS/HTTP 日志 | 判断在线媒体质量。 |
| BLOCKED 率 | runner normalized result | 判断测试环境健康。 |

健康度只用于调度和风险提示，不直接替代功能 PASS/FAIL。

## 11. 回归任务生成

失败场景要能变成回归任务：

```text
failure cluster -> 最小复现场景 -> regression task/scene -> 本地 CLI 回归
```

`build_regression_task.py` 做三件事：

1. 从 `failure_clusters.json` 选择高危或新问题。
2. 裁剪出最小 case sequence。
3. 生成 `tasks/scenes/regression_<signature>.json`。

裁剪规则：

- 如果单用例稳定复现，只保留单用例。
- 如果只在 A -> B 后失败，保留 A、B。
- 如果 network/mode/volume 状态变化是关键，保留造成状态变化的最小节点。
- 如果重试后 PASS，标记为 flaky 回归，不和稳定失败混合统计。

## 12. 本地 CLI 入口

当前阶段不做外部调度集成，只保留稳定、可复制、可审计的本地 CLI 入口：

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py --task <task.json> --print-command
python satellite\cucumber-agent-testing\scripts\run_task.py --task <task.json> --mode execute --allow-side-effects --manage-session
```

外部调度如后续需要，只能作为“调用 CLI + 归档产物”的薄入口，不能写业务判断、不能决定 PASS/FAIL。

## 13. 分阶段实现计划

### Phase 1：统一执行记录和重试控制

目标：先让单用例失败可复现、可重试、可归档。

交付：

- `references/optimization/execution_record.schema.json`
- `references/optimization/retry_policy.json`
- `scripts/run_optimized_task.py`
- 每次执行产出 `execution_record.json`、`attempts.jsonl`、`state/before.json`、`state/after.json`、`state_diff.json`。

验收：

- 跑 `first_wake`，能看到执行前/后状态和日志索引。
- 人为制造一次声卡/串口/网络阻塞，能输出 `BLOCKED` 或 `ENV_RELATED`。
- 同一失败重试 3 次后能标记 `STABLE_FAIL` / `FLAKY_FAIL`。

### Phase 2：场景生成和场景执行

目标：把随机压测、权重压测、指定扰动从项目脚本抽成通用能力。

交付：

- `references/optimization/scene_record.schema.json`
- `references/optimization/scene_strategy_pool.json`
- `scripts/generate_scene.py`
- `scripts/run_scene.py`
- `tasks/scenes/*.example.json`

验收：

- 可生成固定 seed 的场景，重复生成结果一致。
- 可生成 `wakeup -> asr -> network_disconnect -> asr` 这类扰动序列。
- 可跑 WB01/WS63 的在线混合场景，输出 `scene_record.json`。

### Phase 3：失败指纹、聚类和健康度

目标：让失败原因可分析，相似失败可聚类。

交付：

- `references/optimization/failure_signature_rules.json`
- `references/optimization/health_metrics.json`
- `scripts/analyze_execution_store.py`
- `failure_clusters.md`
- `device_health_report.md`
- `flaky_cases.md`

验收：

- 能把 `FAIL_NO_WAKE`、`WARN_MEDIA_ERROR`、`SCRIPT_FALSE_POSITIVE` 分成不同簇。
- 能识别同一个失败在不同 run/版本是否复现。
- 能输出设备健康度和环境阻塞率。

### Phase 4：失败场景回归任务生成

目标：失败场景可回归。

交付：

- `scripts/build_regression_task.py`
- `tasks/scenes/regression_*.json`
- 回归报告模板。

验收：

- 从失败簇自动生成最小回归场景。
- 回归任务由本地 CLI 直接执行；外部调度暂不纳入当前 skill。
- 回归结果能关联原始 `failure_signature`。

### Phase 5：暂缓项

- 外部调度、CI、远程设备池暂不纳入当前 skill。
- 当前优先保证本地真机、Cucumber task、Event Runtime、Replay 和报告稳定。

## 14. 最优先建议

我建议先做 Phase 1 + Phase 2 的最小闭环：

1. `run_optimized_task.py` 包一层当前 `run_task.py`。
2. 每次执行前后调用状态采集，生成 `execution_record.json`。
3. 增加重试和 `STABLE_FAIL / FLAKY_FAIL / ENV_RELATED` 分类。
4. 把在线混合压测的“权重随机袋”抽成 `scene_strategy_pool.json`。
5. 增加 `generate_scene.py` 和 `run_scene.py`，先复用现有 Cucumber tag。

这样收益最大：

- 不会破坏现有 skill。
- 现有 Cucumber 用例全部可复用。
- 失败重试、状态前后快照、场景生成马上能用。
- 后续失败聚类和回归任务可以自然接上。

当前已先落地更底层的 Event Runtime MVP，见 `docs/skill/event-runtime-mvp.md`。它先解决“日志转事件、统一时间轴、状态机、时序断言、离线 replay”这条 Runtime 内核链路，后续再在其上补重试、场景生成和失败分析。

## 15. 和当前测试项沉淀的关系

当前 `docs/skill/supported-test-items-cucumber-guide.md` 解决的是：

```text
每个功能怎么测、怎么断言、怎么写用例。
```

本文方案解决的是：

```text
这些用例怎么被调度、重试、组合成场景、保存上下文、分析聚类、生成回归。
```

两者关系：

```text
测试项知识库 / validation-pool
  -> Cucumber feature/mapping/registry
    -> run_task/run_cucumber 单用例执行
      -> run_optimized_task 采集状态 + 重试 + 结构化记录
        -> generate_scene/run_scene 随机或策略场景
          -> analyze_execution_store 失败聚类 + 健康度
            -> build_regression_task 失败场景回归
```
