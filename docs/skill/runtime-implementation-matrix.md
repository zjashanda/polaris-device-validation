# Runtime 优化方案一致性矩阵

> 2026-06-01 更新：历史 smoke 表中的串口号保留原 run 证据。当前 WS63 映射为 AP `COM20@921600`、upper/asr `COM17@921600`、control `COM19@115200`；当前 WB01 映射为 AP `COM13@921600`、CP `COM14@921600`、WB01/ASR `COM11@921600`、control `COM12@115200`。

本文用于对齐：

- `docs/embedded_validation_runtime_rearchitecture_plan.md`
- `docs/validation_runtime_advanced_optimization_plan.md`
- 当前 skill 已落地代码

## 已完成

| 方案项 | 当前实现 | 主要文件 |
|---|---|---|
| Event Layer | 日志/JSON 产物转 `ValidationEvent` | `runtime/events.py`, `runtime/parsers/*` |
| Timeline | 统一时间线，断言使用 monotonic ms | `runtime/timeline.py` |
| Temporal Assertion | 事件存在、顺序、窗口、禁止事件、profile 断言 | `runtime/assertion_engine.py` |
| Replay Package | 输出 events/timeline/runtime_state/assertions/report | `runtime/replay.py`, `scripts/runtime_replay.py` |
| Runtime Plugin 化 | kernel + wake/asr/media/network/reboot 插件骨架 | `runtime/kernel/*`, `runtime/plugins/*` |
| Event Schema 正式化 | event_version/plugin/tags/severity/wall/monotonic/parent/caused_by | `runtime/events.py` |
| 执行记录闭环 | preflight、attempts、state before/after/diff、execution_record | `scripts/run_optimized_task.py` |
| Resource Runtime MVP | serial/audio/network/cloud/power claim、conflict、local lock/release | `runtime/resource_runtime.py` |
| Constraint Engine MVP | 项目、场景、副作用、串口拓扑、云环境、网络、打断 guard 校验 | `runtime/constraint_engine.py` |
| Parallel State Snapshot | audio/recognition/media/network/power/cloud 并行状态 | `runtime/state_machine.py` |
| State Guard / Coverage MVP | 输出 transitions、state_violations、state_health、coverage；识别 Crash 后业务事件、Reboot 后恢复证据不足、音频/媒体顺序缺口、识别缺少唤醒前因 | `runtime/state_machine.py`, `runtime/replay.py`, `runtime/validation_kernel.py` |
| Scene Graph MVP | strategy -> DAG scene，constraint check，mutation | `runtime/scene_engine.py`, `scripts/generate_scene.py` |
| Scene Runner MVP | scene node 顺序调用 `run_optimized_task.py` | `scripts/run_scene.py` |
| Failure/Health MVP | failure fingerprint、health metrics、报告 | `runtime/failure_analysis.py`, `scripts/analyze_execution_store.py` |
| Replay VM-lite | cursor、snapshot、rollback、time travel 到事件 | `runtime/replay_vm.py`, `scripts/replay_vm.py` |
| Simulation-lite | 生成 Fake log 并可 replay | `runtime/simulation.py`, `scripts/simulate_runtime.py` |
| Assertion DSL-lite | `EXPECT/FORBID`、`EXPECT_SEQUENCE`、`EXPECT_RESPONSE`、`EXPECT_DURATION`，覆盖基础时序和 ASR/Command -> Media/TTS 响应链路 | `runtime/assertion_dsl.py`, `scripts/run_assertion_dsl.py` |
| 真机 session 配置隔离 | `run_cucumber.py` 将 env-file/context 的 AP/CP/ASR/baudrate 传给 managed session，probe/read_lines 优先使用 session_manifest | `scripts/run_cucumber.py`, `tools/device/polaris_serial_harness.py`, `tools/core/polaris_runtime.py`, `tools/core/polaris_config.py` |
| 首唤醒有效音频锚点 | 播放进程明显长于 wav 时长时，使用 `AudioCompleted - audio_duration_ms` 估算有效波形起点；不能估算才输出 `TIMING_AMBIGUOUS` | `runtime/assertion_engine.py`, `runtime/parsers/json_artifact_parser.py` |
| 项目能力降级 | WS63 `cp` 留空时，BDD 与 Runtime 均按 AP/ASR 闭环判断；WB01 仍要求 CP/AP/ASR | `scripts/run_cucumber.py`, `runtime/capabilities.py`, `runtime/replay.py` |
| 优化任务结果聚合 | `run_optimized_task.py` 优先按 scenario/runtime 聚合，不把 `status=DONE` 误判为 PASS | `scripts/run_optimized_task.py` |
| Device Adapter Interface MVP | serial/audio/control/network/cloud adapter registry，不替换 tools，先统一描述能力、资源和动作 | `runtime/device_adapter.py`, `scripts/inspect_device_adapters.py` |
| Capability Runtime MVP | 细粒度推导 AP/CP/ASR、声卡、控制口、PA、网络、云环境、半/全双工、在线媒体、打断、音频回采、媒体响应 oracle、云控权限、重启原因 oracle 等能力 | `runtime/capability_runtime.py`, `scripts/build_capability_matrix.py` |
| Event Graph MVP | 从 Timeline 生成 `audio_caused_wake`、`wake_to_asr`、`asr/command_to_response`、媒体 start->complete、打断、网络恢复、重启/崩溃活动前因和 `risk_summary` | `runtime/event_graph.py`, `scripts/build_event_graph.py` |
| Event Graph Rule Overlay MVP | 项目私有云端/媒体/TTS marker 可通过 JSON overlay 增补因果边，不改核心图逻辑 | `references/optimization/event_graph_rules.json`, `runtime/event_graph.py` |
| State Assertion DSL-lite | 在 `runtime_state.json` 上执行状态断言、历史事件必选/任选、禁止事件 | `runtime/state_assertion_dsl.py`, `scripts/run_state_assertion_dsl.py` |
| State Coverage Policy MVP | 按 profile 检查 `runtime_state.coverage`，输出覆盖阈值 PASS/WARN/FAIL，并接入 Kernel runtime analysis | `runtime/state_coverage_policy.py`, `scripts/run_state_coverage_policy.py` |
| Project Coverage Override MVP | 支持 `coverage.projects.<project_id>` 覆盖 profile/common 阈值，避免项目差异硬编码 | `references/optimization/state_assertion_policy.json` |
| Validation IR MVP | task/scene/compiled feature plan + env 编译为 `polaris.validation_ir.v1` 或 `polaris.validation_ir_bundle.v1`，统一包含 resource/constraint/adapter/capability 与 intent/actions/expect | `runtime/validation_ir.py`, `scripts/compile_validation_ir.py` |
| Analytics Trend MVP | 扫描 `execution_record.json`，按 day/project/task 聚合 result/stability 趋势 | `runtime/analytics_trend.py`, `scripts/build_analytics_trend.py` |
| Adapter Execute Interface MVP | adapter action 渲染命令，默认 dry-run；覆盖控制口 PA/上下电、AP 环境切换、声卡播放、热点状态/恢复、常用云控 API 设置；真执行副作用必须显式 `--execute --allow-side-effects` | `runtime/adapter_executor.py`, `runtime/device_adapter.py`, `scripts/run_adapter_action.py` |
| Adapter Flow Map MVP | 常见前置/调试流程映射到 adapter action 序列，默认 dry-run 渲染命令 | `references/adapter_flow_map.json`, `scripts/plan_adapter_flow.py` |
| Optimized Task Adapter Flow Bridge | `run_optimized_task.py` 支持 `execution.adapter_flows.pre/post`，把前置/收尾动作纳入 execution_record | `scripts/run_optimized_task.py` |
| 在线全双工任务沉淀 | `online_full_duplex.example.json` 串联 laid 检查、设备 UAT/SIT 环境切换、联网确认、全双工 API 和 `@full_duplex_recognition` runtime 断言；FD-002~FD-012 已拆成连续对话、媒体打断、超时边界、异常矩阵和随机稳定性 task/scene | `tasks/examples/online_full_duplex*.example.json`, `references/scenes/online_full_duplex_fd002_fd012.scene.example.json`, `docs/wiki/voice-validation/packs/online-full-duplex.md` |
| Adapter-only Long Runner Bridge | 历史长流程 runner 不再直接调用声卡/串口/控制口/网络 helper；统一经 `polaris_adapter_bridge.py` -> Adapter Executor 执行，并用 `audit_adapter_only.py` 防回退 | `tools/core/polaris_adapter_bridge.py`, `scripts/audit_adapter_only.py`, `scripts/run_online_mixed_stress.py`, `tools/validation/polaris_fa2_command_batch.py` |
| Validation Kernel Lifecycle MVP | compile_ir、preflight、adapter/capability/resource/constraint 快照、可选 run_optimized_task、kernel_record/lifecycle；真机 replay 存在时自动生成 event graph、state assertions、Replay VM-lite snapshot | `runtime/validation_kernel.py`, `scripts/run_validation_kernel.py` |
| Kernel Scene Scheduler MVP | scene 每个节点走独立 Kernel 生命周期，输出 scene 级记录和节点级 kernel_record | `scripts/run_kernel_scene.py` |
| 默认状态断言策略 | 按 runtime profile 自动追加 WakeDetected、ASR/Command、媒体响应、禁止 Crash/Reboot/误唤醒等兜底断言 | `references/optimization/state_assertion_policy.json` |

## 部分完成

| 方案项 | 当前状态 | 后续差距 |
|---|---|---|
| Validation Kernel | 已有 plugin kernel、Validation IR MVP、本地 lifecycle runner、runner 后 replay/event graph/state/replay_vm 侧证据、scene scheduler，并可接 Adapter Flow/Adapter-only runner | 更完整的 agent planner/replay 入口仍是后续扩展 |
| Event Graph | 已有本地因果图 MVP，已覆盖媒体响应、媒体完成、打断、网络恢复、重启/崩溃前因、风险摘要和项目规则 overlay | 事件因果仍是启发式，项目私有云端协议链路需要继续补 overlay 规则 |
| Hierarchical StateMachine | 已有并行状态快照、迁移记录、guard 违规、覆盖率、State Assertion DSL-lite 和 coverage 阈值策略 MVP | 尚未实现完整层级状态树；项目私有 profile 阈值还需随实机资料细化 |
| Capability Runtime | 已有项目能力矩阵 MVP，已显式列出音频回采、媒体响应 oracle、云控配置权限、boot reason oracle 缺口 | codec、真实出声质量评分、具体云 API token 权限校验仍需项目资料或实机接口 |
| Device Adapter Layer | 已有 adapter registry、adapter action executor、adapter flow、adapter-only 长流程桥和审计脚本 | 后续新增 runner 必须进入 `audit_adapter_only.py`；新硬件动作需要先补 adapter action |
| IR Compiler | 已有 task、scene node、scene bundle、compiled feature plan bundle 的 Validation IR MVP | agent planner/replay 入口仍是后续扩展，当前 runtime 已不直接执行自然语言 feature |
| Validation DSL Compiler | 已有 Assertion DSL-lite 和 State Assertion DSL-lite，可表达常见事件存在、窗口、序列、响应、持续时长、状态禁止 | 尚未替代 Python profile 断言，也未形成完整自然语言 DSL 编译器 |
| Analytics Pipeline | 已有本地 trend MVP | 尚未流式化，也未接长期指标库/看板 |

## 明确暂缓

| 方案项 | 暂缓原因 |
|---|---|
| Distributed Runtime | 当前只做本地真机 skill，不做远程 worker/device pool |
| 大规模 Failure Clustering | 当前先做 fingerprint/health MVP，不做大规模聚类平台 |
| 完整 Replay VM | 当前只有 VM-lite，不做完整 snapshot rollback/fault injection |
| 完整 Device Simulation | 当前只有 Fake log，不模拟云端协议/ASR 模型/媒体栈 |
| 完整 Validation DSL Compiler | 当前 DSL-lite 已覆盖常见业务链路，但不替代 Python profile 断言 |
| Runtime Plugin Sandbox | 当前插件同进程执行，暂不做进程级隔离和内存限制 |

## 当前优先验证路径

1. `run_optimized_task.py --precheck-only`：验证 env/task/resource/constraint。
2. `run_optimized_task.py --mode dry-run`：验证 Cucumber runner 不被破坏。
3. `generate_scene.py` + `run_kernel_scene.py --print-command`：验证 scene graph 和 Kernel 调度串联。
4. `simulate_runtime.py --replay`：验证 Simulation-lite + Replay。
5. `run_assertion_dsl.py`：验证 DSL-lite。
6. `inspect_device_adapters.py` + `build_capability_matrix.py`：验证项目 adapter/capability。
7. `compile_validation_ir.py`：验证 task/env 能编译到稳定 IR。
8. `build_event_graph.py` + `run_state_assertion_dsl.py`：验证因果图和状态断言。
9. `build_analytics_trend.py`：验证本地执行记录趋势汇总。
10. `run_adapter_action.py`：验证 adapter action 只规划命令或显式执行。
11. `run_validation_kernel.py`：验证 Kernel 生命周期记录、可选 runner 委托和 runner 后侧证据补齐。
12. `run_kernel_scene.py`：验证 scene 每个节点通过 Kernel 生命周期执行。
13. WB01/WS63 分别执行 precheck/dry-run，小规模 true-device smoke 按需执行。

## 2026-05-26 真机验证记录

| 项目 | 场景 | 端口来源 | BDD 断言 | Runtime 断言 | 结论 |
|---|---|---|---|---|---|
| `cskwb01` | `first_wake` | env-file/context -> managed session，COM13/COM12/COM14 | CP/AP/ASR 唤醒闭环，播放 returncode=0 | `WakeDetected_within_3000ms` PASS，估算有效波形起点后约 1062ms | PASS |
| `venusws63` | `first_wake` | env-file/context -> managed session，COM20/COM16，CP 留空 | AP/ASR 唤醒闭环，播放 returncode=0 | `WakeDetected_within_3000ms` PASS，估算有效波形起点后约 895ms | PASS |

说明：两次真机 smoke 都遇到主机播放进程耗时明显大于 1266ms wav 时长的情况，因此 Runtime 使用 `AudioCompleted - audio_duration_ms` 作为有效波形起点；这与方案中的 Timeline/Temporal Assertion 思路一致，避免把播放工具初始化耗时误归因为固件唤醒超时。

## 2026-05-26 剩余本地化能力补齐验证

| 能力 | WB01/WS63 验证 |
|---|---|
| Adapter Registry | WB01 adapters=7/warnings=0；WS63 adapters=7/warnings=2（CP 空、部分在线配置缺口按能力矩阵展示） |
| Capability Matrix | WB01 supported=20；WS63 supported=13/config_required=6/not_applicable=1 |
| Event Graph | WB01 nodes=25/edges=32/warnings=0；WS63 nodes=34/edges=46/warnings=0 |
| State Assertion DSL | WB01/WS63 均 PASS，断言 WakeDetected history 存在且 power/final_state 未 CRASHED |
| Validation IR | WB01/WS63 `first_wake` IR constraints 均 PASS；scene/feature plan bundle 已做 dry-run 编译 smoke |
| Analytics Trend | 本地 optimized_runs 历史扫描 records=11，结果只作为本机历史样本，不提交 |

## 2026-05-26 Kernel 生命周期补齐验证

| 能力 | 验证结果 |
|---|---|
| Adapter Action Executor | WB01 `control.serial/send_control command=uut-pa.on` dry-run 渲染命令成功，PLAN_OK，未真实执行 |
| Validation Kernel Lifecycle | WB01 `first_wake` dry-run + execute-runner PASS，输出 `kernel_record.json` / `lifecycle.jsonl` |
| Validation Kernel Lifecycle | WS63 `first_wake` dry-run + execute-runner PASS，输出 `kernel_record.json` / `lifecycle.jsonl` |

## 2026-05-26 Kernel Scene 与 Replay 后处理验证

| 能力 | 验证结果 |
|---|---|
| State Assertion DSL 扩展 | `EXPECT_ANY_HISTORY` / `FORBID_HISTORY` 已纳入 compileall 和状态断言策略 smoke |
| Kernel runner 后处理 | 使用既有 WB01 真机 execution_record 发现 runtime replay package，自动生成 `runtime_analysis.json`，state assertion PASS |
| Kernel Scene Scheduler | WB01 `scene_smoke` dry-run + execute-runner 3 节点 PASS，节点均输出独立 `kernel_record.json` |
| Kernel Scene Scheduler | WS63 `scene_smoke` dry-run + execute-runner 3 节点 PASS，节点均输出独立 `kernel_record.json` |

## 2026-05-26 StateMachine Guard/Coverage 验证

| 能力 | 验证结果 |
|---|---|
| State Guard / Coverage | 使用 WB01 既有 `first_wake` 真机日志重放，`state_health=PASS`，`transition_count=24`，`violation_count=0` |
| Kernel runtime_analysis | `runtime_analysis.md/json` 已汇总 `state_health`、`state_violation_count`、`transition_count`，用于区分稳定性问题、日志缺口和业务断言失败 |

## 2026-05-26 Assertion DSL 业务链路验证

| 能力 | 验证结果 |
|---|---|
| `EXPECT_SEQUENCE` | WB01 `first_wake` replay 中验证 `WakeDetected -> ASRDetected -> MediaStarted WITHIN 3000ms` PASS |
| `EXPECT_RESPONSE` | WB01 `first_wake` replay 中验证 `ASRDetected|CommandDetected` 后 `MediaStarted|TTSStarted` 1500ms 内响应 PASS |
| `EXPECT_DURATION` | WB01 `first_wake` replay 中验证 `MediaStarted TO MediaCompleted >= 50ms` PASS |

## 2026-05-26 Capability Runtime 细化验证

| 能力 | 验证结果 |
|---|---|
| Capability Matrix 细化 | 新增 `audio.loopback_oracle`、`media.response_log_oracle`、`media.acoustic_response_oracle`、`cloud.volume_control/night_mode/wake_word_config/wake_threshold/multi_wake`、`reboot.boot_reason_oracle` |
| WB01 | 细化后 summary：`supported=21`、`config_required=8` |
| WS63 | 细化后 summary：`supported=14`、`config_required=14`、`not_applicable=1` |

## 2026-05-26 Event Graph 因果补强验证

| 能力 | 验证结果 |
|---|---|
| WB01 first_wake replay | `nodes=25`、`edges=38`、`warnings=0`，新增 `media_started_to_completed` / `asr_to_media_response` 等关系和 `risk_summary` |
| WS63 first_wake replay | `nodes=34`、`edges=48`、`warnings=0`，新增 `asr_to_tts_response`、网络 loss 计数和媒体响应关系 |
| 合成打断/重启 smoke | 可推导 `media_interrupted`、`interrupt_injected_to_completed`、`interrupt_to_recognition`、`possible_reboot_after_activity` |

## 2026-05-26 Adapter Executor 覆盖验证

| 能力 | 验证结果 |
|---|---|
| Adapter Registry | WB01 `adapters=7/actions=25/warnings=0`；WS63 `adapters=7/actions=21/warnings=2` |
| Control actions | `pa_on`、`power_on` dry-run 均 PLAN_OK，命令渲染到控制口 |
| Env/Cloud/Network/Audio actions | `serial.ap/set_device_env`、`cloud.api/set_volume`、`network.local/hotspot_status`、`audio.playback/play` dry-run 均 PLAN_OK |

## 2026-05-26 Validation IR 统一入口验证

| 能力 | 验证结果 |
|---|---|
| Task IR | WB01 `first_wake.example.json` + env 编译为 `polaris.validation_ir.v1`，constraints=PASS |
| Scene IR Bundle | WS63 `scene_smoke.json` 编译为 `polaris.validation_ir_bundle.v1`，items=3，constraints=PASS |
| Kernel Scene IR Bundle | `run_kernel_scene.py --emit-ir-bundle --print-command` 输出 `scene_validation_ir_bundle.json`，items=3，PLAN_OK |
| Feature Plan IR Bundle | `compile_feature.py --tag first_wake` 后再 `compile_validation_ir.py --feature-plan`，输出 feature bundle，items=1，constraints=PASS |

## 2026-05-26 State Coverage Policy 验证

| 能力 | 验证结果 |
|---|---|
| Coverage policy CLI | 旧版 first_wake runtime_state 可从 history 派生 coverage，输出 WARN（识别并行状态覆盖不足），不误报脚本崩溃 |
| Coverage policy CLI | 新版 state_machine fake_run runtime_state 输出 PASS，WakeDetected/Crash/Reboot/parallel recognition 检查均通过 |
| Kernel 集成 | Kernel 后处理会为每个 replay package 写 `state_coverage_policy.json`，并在 `runtime_analysis.json` 聚合 `state_coverage_result` |

## 2026-05-26 Adapter Flow Map 验证

| 能力 | 验证结果 |
|---|---|
| WB01 PA 前置 | `plan_adapter_flow.py --flow pa_recover` 渲染 `pa_on` + `pa_persist`，均 PLAN_OK |
| WB01 环境切换 | `--flow switch_device_env` 渲染 AP `set_device_env`，PLAN_OK |
| WB01 云控音量 | `--flow set_volume --param volume=35` 渲染 cloud API 命令，PLAN_OK |
| WS63 声卡播放 | `--flow wake_audio_file --param audio_file=sample.wav` 渲染 listenai-play + WS63 声卡 key，PLAN_OK |

## 2026-05-27 Optimized Task Adapter Flow Bridge 验证

| 能力 | 验证结果 |
|---|---|
| print-command | 带 `execution.adapter_flows.pre/post` 的临时 task 可打印 pre/post adapter flow 命令和主 `run_task.py` 命令 |
| dry-run 执行 | WB01 临时 task pre=`pa_recover`、`switch_device_env`，post=`hotspot_status`，主流程 dry-run PASS |
| execution_record | `execution_record.json` 已记录 `adapter_flows.pre/post`，pre/post 均 PLAN_OK，主 attempts=1 |

## 2026-05-27 Event Graph Rule Overlay 验证

| 能力 | 验证结果 |
|---|---|
| 默认规则文件 | 新增 `references/optimization/event_graph_rules.json`，默认示例规则 disabled，不影响既有图 |
| CLI overlay | `build_event_graph.py --rules <file>` 支持加载项目规则 |
| Overlay smoke | WS63 first_wake timeline + smoke rule 输出 `rule_edge_count=7`、`smoke_asr_to_response_overlay=7`、warnings=0 |
| Kernel 集成 | Validation Kernel 后处理默认加载 `event_graph_rules.json`，项目启用规则后会进入 `runtime_analysis` 侧证据 |

## 2026-05-27 Project Coverage Override 验证

| 能力 | 验证结果 |
|---|---|
| Policy merge | `coverage.projects.<project_id>.profiles.<profile>` 可覆盖通用 profile 阈值 |
| CLI project-id | `run_state_coverage_policy.py --project-id cskwb01` 输出结果带 project_id |
| Override smoke | 自定义 cskwb01 first_wake `min_transition_count=999` 输出 WARN；venusws63 未命中 override 保持 PASS |
| Kernel 集成 | Validation Kernel 后处理会把 package metadata project 或当前 IR project_id 传入 coverage policy |

## 2026-05-27 Adapter-only 长流程 runner 验证

| 能力 | 验证结果 |
|---|---|
| Bridge | `tools/core/polaris_adapter_bridge.py` 统一封装音频播放、低延迟播放、控制口 PA/上下电、网络动作和后台串口 logger session |
| Runner migration | `run_online_mixed_stress.py`、`run_wake_stress.py`、`run_oneshot_matrix.py`、`run_network_recovery_basic.py`、`measure_interrupt_prerequisites.py`、FA2 batch、case/doc runner 播放入口已改为经 Adapter Executor |
| Audit | `audit_adapter_only.py` 检查 12 个历史长流程/执行入口，`finding_count=0`，结果 PASS |
| Adapter registry | WB01 `adapters=8/actions=39/warnings=0`；WS63 `adapters=8/actions=38/warnings=2`（CP 空、Wi-Fi/热点配置缺口按 warning 展示） |
| Dry-run smoke | `serial.logger/start`、`audio.playback/play_skip_probe`、`audio.playback/laid_list`、`network.local/hotspot_on`、`control.serial/cycle_target_window` 均可渲染为 PLAN_OK，未触发真实副作用 |
| laid self-contained | `tools/audio/polaris_laid.py check/list` 已验证可用；Windows/Linux 安装脚本归档在 `tools/audio/laid/`，新电脑可先 `ensure` 再查询声卡 key |
