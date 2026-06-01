# 需求到执行闭环工作流

本文是 Polaris skill 的标准工作方式。后续用户提出测试需求时，不再按零散调试方式逐步抛中间产物，而是按“需求解读 -> 方案和用例 -> 用户确认 -> 真机执行 -> 结果分析总报告”的闭环推进。

## 1. 总流程

```text
用户需求/需求文档
  -> 需求解读
  -> 测试方案
  -> 测试用例包
  -> 缺口与风险确认
  -> 用户确认执行
  -> Cucumber/Runtime 真机执行
  -> 日志/事件/replay/断言分析
  -> 总报告
```

默认只在两个节点向用户集中输出：

1. 执行前：方案、用例、断言、缺口，需要用户确认。
2. 执行后：总结果、失败归因、证据路径、后续优化建议。

除非遇到会影响执行安全或结论有效性的阻塞问题，中间不反复输出零散进度。

## 2. 需求输入

用户可以用两种方式给需求：

- 直接口述：例如“我要验证在线全双工”“我要测唤醒率”“我要压测在线问答和音乐”。
- 提供资料：把需求文档、表格、外部测试方案、日志样例放到 `docs/intake/<project_id>/<YYYYMMDD_topic>/`，并填写 `learning_manifest.json`。

如果需求只有一句话，agent 需要自行补全测试思路；如果资料不足，只在“缺口与风险”里列出，不应直接伪造测试结论。

需求解读前必须先查当前 Wiki：

- `docs/wiki/voice-validation/test-item-index.md`：确认测试项、能力等级、已有执行入口和缺口。
- `docs/wiki/voice-validation/assertion-attribution.md`：确认 PASS/FAIL/BLOCKED/时序不明确/需求复核口径。
- `docs/wiki/voice-validation/packs/`：如果已有验证包，优先复用，不要每次重新设计脚本。
- 对应专题 Wiki：`wakeup.md`、`command.md`、`free-speech.md`、`online-recognition.md`、`false-wake.md`。

## 3. 需求解读输出

需求解读必须回答：

- 要验证的功能意图是什么。
- 适用项目和设备拓扑是什么。
- 依赖哪些前置：联网、UAT/SIT、声卡、PA、串口、云控 API、唤醒词、超时时间。
- 哪些能力已经在 registry/runtime 中沉淀，哪些需要新增。
- 哪些需求口径不明确，会影响 PASS/FAIL 判断。

已沉淀验证包命中的需求，可以先用固定脚本生成审查包：

```powershell
python satellite\cucumber-agent-testing\scripts\generate_requirement_package.py --requirement "在线全双工相关功能验证"
```

输出包含 `test_plan.md`、`case_matrix.md`、`gap_list.md`、`confirmation.md`，用于进入“用户确认”阶段；执行阶段仍必须走 Cucumber/Task/Adapter/Runtime。

## 4. 测试方案输出

测试方案至少包含：

- 测试目标。
- 测试范围和不测范围。
- 环境和配置。
- 测试数据与语料。
- 执行流程。
- 断言策略。
- 归因策略。
- 风险和缺口。

对于语音功能，必须覆盖：

- 正例：按需求输入，应成功。
- 反例：不该触发时不能触发。
- 异常：断网、API 失败、串口/声卡失败、设备重启、日志缺失。
- 边界：超时临界、播报占用、连续交互、低间隔或高频触发。
- 稳定性：多轮、随机、长时间或组合压测，按需求决定是否纳入。

## 5. 测试用例包输出

测试用例包不是只给一条 smoke。除非用户明确说“只要 smoke”，否则需要按场景完整拆分。

每条用例至少包含：

| 字段 | 说明 |
| --- | --- |
| 用例 ID | 稳定编号，例如 `FD-001` |
| 类型 | 正例/反例/异常/边界/稳定性 |
| 目标 | 本用例验证什么 |
| 前置 | 设备状态、联网、配置、串口、声卡 |
| 步骤 | 可执行动作和时间间隔 |
| 期望证据 | 串口/API/音频/Runtime 应出现什么 |
| 禁止证据 | 不应出现什么 |
| 断言 | PASS/FAIL/BLOCKED/TIMING_AMBIGUOUS/REQUIREMENT_REVIEW 判定 |
| 自动化状态 | 已可执行/需新增脚本/需资料/人工复核 |

## 6. 用户确认

执行前必须让用户确认：

- 用哪台设备/哪个 `active_project`。
- 是否允许真机副作用：串口占用、声卡播放、云控配置、上下电、断网/联网。
- 是否按当前方案全量执行，还是只执行某些用例。
- 是否允许修改设备状态，例如切全双工、恢复半双工、改音量、改夜间模式。

确认前可以做 dry-run/precheck，但不能把 dry-run 说成真机验证完成。

## 7. 执行

执行必须优先走确定性入口：

- 单功能：`run_optimized_task.py`
- Cucumber：`run_task.py` / `run_cucumber.py`
- 多场景/策略：`run_kernel_scene.py` 或已沉淀 runner
- Adapter 前置：`plan_adapter_flow.py`
- 结果复核：`runtime_replay.py`、Event Graph、StateMachine、coverage policy
- 总报告汇总：`build_validation_summary_report.py`

执行期间：

- 不依赖大模型动态改脚本。
- 不跳过已声明的断言。
- 不把环境阻塞判成固件 FAIL。
- execute 前如果串口被 Xshell/串口助手/旧 logger 占用，必须先判 `BLOCKED` 并释放端口。
- 记录额外 wake/ASR/command，作为误唤醒/误识别候选。

## 8. 结果分析总报告

执行后只输出总报告，至少包含：

- 总体结论：通过/失败/阻塞/时序不明确。
- 用例统计：总数、通过数、失败数、阻塞数、未执行数。
- 每条用例结论。
- 关键失败归因。
- 证据目录。
- 设备稳定性：reboot/crash/watchdog/panic。
- 异常识别：额外 wake/ASR/command。
- 交互链路：每轮尽量给出 `唤醒 -> 识别 -> 云端请求 -> 设备响应` 的一一对应关系。
- 识别信息：记录期望语料、实际识别中文、识别拼音、本地 keyword/拼音以及额外误识别候选。
- 在线请求信息：在线场景必须记录 `mid`、`sessionId`、`recordId`、`topic`、`deviceId`、云端响应类型、TTS/media URL、来源日志和行号；后续可用这些字段去云端捞对应音频。
- 链路耗时信息：WB01/WS63 都要尽量记录 `wake_to_recognition_ms`、`wake_to_cloud_request_ms`、`cloud_request_to_recognition_ms`、`cloud_request_to_first_cloud_response_ms`、`cloud_request_to_audio_broadcast_ms`、`cloud_request_to_speech_reply_ms`、`recognition_to_first_cloud_response_ms`、`recognition_to_audio_broadcast_ms`、`recognition_to_speech_reply_ms`、`recognition_to_tts_start_ms`、`recognition_to_media_start_ms`、`first_cloud_response_to_tts_start_ms`、`first_cloud_response_to_media_start_ms`、`audio_broadcast_to_tts_start_ms`、`audio_broadcast_to_media_start_ms`、`tts_start_to_media_start_ms`、`tts_or_media_play_duration_ms`；时间戳缺失或日志 marker 不存在时保留 `limitations/evidence_gap`，不能伪造耗时。
- 需求问题：需要用户确认的口径。
- 后续建议：新增用例、补资料、修脚本、复测。

当前在线混合压测和命令控制诊断结果会把上述信息写入单轮 `result.json` 的 `metrics.interaction_trace` / `metrics.online_request_ids` / `metrics.latency_samples`，并在 `rounds.csv` / `matrix.csv` 中输出常用列；历史日志可用下面命令补抽：

```powershell
python satellite\cucumber-agent-testing\scripts\extract_online_request_ids.py --log <COMxx.log>
```

## 9. 未完全落地时的处理

如果用户提出的需求覆盖了当前未沉淀能力，agent 应先补齐可执行沉淀：

1. 方案和用例草案。
2. Cucumber feature/tag。
3. step/action/assertion registry 或 mapping。
4. task 示例。
5. runtime profile/assertion 或复用现有 profile。
6. dry-run/precheck。

只有完成这些后，才进入真机执行。

如果补齐过程中形成了通用测试方法、失败模式或断言规则，必须同步沉淀到 `docs/wiki/`；如果只是项目私有配置、日志 marker 或能力差异，则沉淀到 `docs/knowledge/<project_id>/`。

## 10. 失败反哺和媒体响应补充流程

真机执行后如果出现 `FAIL`、`BLOCKED`、`TIMING_AMBIGUOUS`，必须优先把失败沉淀成可复测资产：

```powershell
python satellite\cucumber-agent-testing\scripts\generate_failure_case.py --run <run_or_optimized_dir>
```

输出包括：

- `failure_case_package.json`：结构化失败候选。
- `candidate_cases.md`：候选 Gherkin 回归用例。
- `suggested_registry_updates.md`：断言、Event Graph、constraint 补强建议。
- `retest_checklist.md`：复测前置检查清单。

候选不能直接自动落库，必须先人工确认；确认后再执行：

```powershell
python satellite\cucumber-agent-testing\scripts\register_failure_case.py `
  --package <failure_case_package.json> `
  --approve --approved-by <name>
```

注册后会写入：

- `satellite/cucumber-agent-testing/references/failure_regression_registry.json`
- `satellite/cucumber-agent-testing/tasks/generated/regression/`
- `satellite/cucumber-agent-testing/references/scenes/generated_failure_regression.scene.example.json`
- `docs/wiki/voice-validation/failure-patterns/`

在线音乐、新闻、相声、问答、TTS 或 MP3 场景，报告中必须补充媒体响应 oracle：

```powershell
python satellite\cucumber-agent-testing\scripts\analyze_media_response_oracle.py --run <run_dir>
```

v1 oracle 只做日志/事件级判断，区分云端/TTS 响应、播放器启动、播放完成、HTTP/player 错误、重启/崩溃；真实声学播放需要配置 loopback/capture 并执行 `tools/audio/polaris_acoustic_oracle.py`。
