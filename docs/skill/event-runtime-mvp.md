# Event Runtime MVP 落地说明

本文记录 `embedded_validation_runtime_rearchitecture_plan.md` 的第一阶段落地结果。当前目标不是替换 Cucumber，而是在现有 Cucumber/Registry/Validation Pool 下方增加 deterministic runtime 内核雏形。

## 已落地范围

当前已新增：

```text
satellite/cucumber-agent-testing/runtime/
  events.py                  # 标准 ValidationEvent 数据结构
  timeline.py                # 统一时间轴、事件计数、source group
  state_machine.py           # IDLE/LISTENING/ASR/TTS/MEDIA/REBOOT 等状态迁移
  assertion_engine.py        # event_exists/event_within/no_event/order 时序断言
  capabilities.py            # 按项目配置推导 cp_log/asr_log 等能力
  parsers/serial_log_parser.py
                             # AP/CP/ASR/WS63/playback 日志 -> Event
  replay.py                  # replay package 生成
satellite/cucumber-agent-testing/scripts/runtime_replay.py
                             # 离线解析已有日志并执行 runtime assertion
```

同时，`tools/execution/polaris_case_runner.py::run_playback` 已开始写入 `*_runtime_events.jsonl` 旁路事件文件。后续新跑的 Cucumber/phrase-probe 类用例会带有 `AudioInjected/AudioCompleted` 结构化事件；旧历史日志没有该文件时仍可 replay，但注入后耗时断言会被标记为 `SKIP`。

第一批支持事件：

| Event | 来源示例 |
| --- | --- |
| `AudioInjected` | 播放日志 `Play iteration`，后续真机 runner 可直接写入。 |
| `AudioCompleted` | 播放日志 `Playback finished`。 |
| `WakeDetected` | CP `WAKE(1)`、AP `Pre Wakeup/wakeup_callback`、ASR/upper `offline_wakeup/online_wakeup`。 |
| `ASRDetected` | ASR cmd `0x1005/0x1006`、`online_asr_callbak`、cloud ASR ack。 |
| `CommandDetected` | CP `WAKE(0)`、非唤醒 keyword、`online_asr_callbak text`、cloud ASR ack。 |
| `TTSStarted` | `offline_tts_callbak`、`audioBroadcast`、`TTS recv`、`stream_tts`。 |
| `MediaStarted` | `play next tone`、`soundplayer status: 2`。 |
| `MediaCompleted` | `soundplayer status: 6`、`PLAYBACK_COMPLETE`。 |
| `NetworkLost` | `WiFi disconnected` 等网络断开 marker。 |
| `NetworkRecovered` | `online=true`、`cloud status :0x04` 等在线 marker。 |
| `RebootDetected` | `Boot Reason`、`VENUSA BOOT`。 |
| `CrashDetected` | `watchdog/panic/hardfault/fatal/crash/assert failed`，排除 `ignore exception` 和播放器 reset 误判。 |

当前支持 profile：

| profile | 断言 |
| --- | --- |
| `first_wake` | 必须有 `WakeDetected`；按 capability 要求 AP/CP/ASR 或 AP/upper 证据；如有 `AudioInjected`，检查 wake 是否在 3000ms 内；wake 后 10s 内无 reboot/crash。 |
| `recognition_mode_wake` | 先把 AP/CP/ASR 的重复 wake marker 聚成物理唤醒簇；选择与测试音频时间线匹配的相邻唤醒簇；第二簇必须落在识别超时安全区内；临界超时灰区输出 `TIMING_AMBIGUOUS`，不直接判固件 FAIL。 |
| `basic_command` | 必须有 `WakeDetected`、`ASRDetected`、`CommandDetected`；`WakeDetected` 必须早于 `CommandDetected`；wake 后 10s 内无 reboot/crash。 |
| `half_duplex_recognition` / `full_duplex_recognition` | 解析 doc case `judge.json` 中的 `cloud_apply_success`、半/全双工 timeout 刷新值和成功响应计数，并叠加 wake/ASR/command/no reboot/no crash 闭环断言。 |
| `command_batch` / `command_batch_exploratory` | 解析 fa2 批量 summary；检查播放返回、逐条语料 PASS、唤醒证据、ASR/命令/意图证据；探索性自由说缺正式 oracle 时避免误归固件 FAIL。 |
| `interrupt_prerequisite_measurement` | 解析打断前置测量 summary；检查候选播放、可用候选数量、选中自播窗口时长、注入偏移、唤醒/识别证据和无 reboot/crash。 |
| `wake_interrupt` | 解析 `InterruptInjected` 与 `MediaStarted/MediaCompleted` 自播窗口；注入必须落在窗口保护区内；命中后 5s 内应有新的 wake；未命中窗口输出 `TIMING_AMBIGUOUS`。 |
| `command_interrupt` | 与 `wake_interrupt` 相同先验证注入窗口；命中后 5s 内应有 `ASRDetected` 或 `CommandDetected`；窗口未命中时不判固件 FAIL。 |
| `network_recovery_basic` | 必须先观察 `NetworkLost`，再观察 `NetworkRecovered`；恢复后 60s 内需要有在线语音闭环证据（ASR + TTS/media/command）；恢复窗口内无 reboot/crash。 |
| `offline_oneshot_matrix` / `online_oneshot_matrix` | 解析 one-shot 矩阵 summary；逐间隔检查 PASS、唤醒证据、离线命令证据或在线 ASR/TTS/media 证据。 |
| `wake_matrix` | 解析唤醒矩阵 summary；检查有效轮次、PASS 率、逐轮三端唤醒证据、reboot/crash marker、响应时间 proxy 统计。 |
| `online_vad_special` | 解析在线 VAD summary；逐候选检查唤醒证据、在线 ASR/VAD end/云端播报证据；文本覆盖差异作为探索性复核。 |
| `false_wake_quiet` / `false_wake_playback` | 解析误唤醒 summary 和干扰音频播放证据；检查监听窗口无 wake marker、串口非静默、无 reboot/crash。 |
| `attribution_validator` | 解析归因复核 summary；检查扫描 run 数、ERROR 级归因不一致数量和 finding 事件。 |

## 为什么先做离线 replay

第一步选择离线 replay，不直接改真机执行链路，原因：

- 不占串口、不播放音频，不影响当前设备调试。
- 可以用已有 WB01/WS63 真机日志验证 parser 和 assertion 是否正确。
- 可以先修正日志 marker 误判，再接入 Cucumber execute。
- 产物结构与后续 Replay System 一致。

## 使用方式

WB01 首次唤醒日志 replay：

```powershell
python satellite\cucumber-agent-testing\scripts\runtime_replay.py `
  --input-dir result\dual_device_bringup_20260525_140406\wb01 `
  --project cskwb01 `
  --profile first_wake `
  --out-dir satellite\cucumber-agent-testing\debug\runtime_replay\wb01_first_wake_final `
  --strict-result
```

WS63 首次唤醒日志 replay，无 CP，按 `venusws63` capability 自动降级：

```powershell
python satellite\cucumber-agent-testing\scripts\runtime_replay.py `
  --input-dir result\ws63_retest_20260525_143445\normal_after_rewire `
  --project venusws63 `
  --profile first_wake `
  --out-dir satellite\cucumber-agent-testing\debug\runtime_replay\ws63_first_wake_final `
  --strict-result
```

WB01 命令词链路 replay：

```powershell
python satellite\cucumber-agent-testing\scripts\runtime_replay.py `
  --input-dir result\wb01_debug_20260521_154536\artifacts\misc\wb01\20260521155237059_wb01_wake_command_smoke\window_logs `
  --project cskwb01 `
  --profile basic_command `
  --out-dir satellite\cucumber-agent-testing\debug\runtime_replay\wb01_basic_command_final `
  --strict-result
```

识别模式唤醒 replay：

```powershell
python satellite\cucumber-agent-testing\scripts\runtime_replay.py `
  --input-dir <run_or_log_dir> `
  --project cskwb01 `
  --profile recognition_mode_wake `
  --out-dir satellite\cucumber-agent-testing\debug\runtime_replay\recognition_mode_wake_debug
```

也可以从根配置推导 capability：

```powershell
python satellite\cucumber-agent-testing\scripts\runtime_replay.py `
  --input-dir <log_dir> `
  --env-file polaris.local.json `
  --project venusws63 `
  --profile first_wake
```

注意：如果不传 `--project`，`--env-file` 会使用 `active_project`。输入 WS63 日志但 active project 是 WB01 时，会按 WB01 三端断言要求 CP，结果会 FAIL，这是预期行为。

## 本轮验证结果

| 输入日志 | profile | 结果 | 说明 |
| --- | --- | --- | --- |
| `result/dual_device_bringup_20260525_140406/wb01` | `first_wake` | `PASS_WITH_SKIPPED_TIMING` | AP/CP/ASR wake 均有；离线日志没有 `AudioInjected`，所以耗时断言 SKIP。 |
| `result/ws63_retest_20260525_143445/normal_after_rewire` | `first_wake` | `PASS_WITH_SKIPPED_TIMING` | AP/WS63 wake 均有；WS63 无 CP，capability 自动降级。 |
| `result/dual_device_bringup_20260525_140406/ws63_retry_pa` | `first_wake` | `FAIL` | 只有网络断开日志，无 wake event，符合当时接线未修复现象。 |
| `result/wb01_debug_20260521_154536/.../wake_command_smoke/window_logs` | `basic_command` | `PASS` | wake、ASR、command、TTS/media 事件均可提取。 |
| synthetic log with `AudioInjected` | `first_wake` | `PASS` | `WakeDetected` 在 850ms 内发生，验证时序断言可工作。 |
| synthetic log with two wake clusters | `recognition_mode_wake` | `PASS` | 两个物理唤醒簇间隔 5350ms，落在 15s 超时的安全区内。 |
| synthetic boundary log | `recognition_mode_wake` | `TIMING_AMBIGUOUS` | 第二次唤醒落在 `timeout - guard` 附近，按灰区处理，不误判固件。 |
| synthetic interrupt wake log | `wake_interrupt` | `PASS` | 注入点在自播窗口保护区内，注入后观察到 wake。 |
| synthetic interrupt command log | `command_interrupt` | `PASS` | 注入点在自播窗口保护区内，注入后观察到 ASR/命令证据。 |
| synthetic interrupt boundary log | `wake_interrupt` | `TIMING_AMBIGUOUS` | 注入只落在自播窗口附近，按时序不明确处理。 |
| synthetic network recovery log | `network_recovery_basic` | `PASS` | `NetworkLost -> NetworkRecovered -> ASR/TTS/media` 顺序和 60s 窗口断言通过。 |
| `satellite/cucumber-agent-testing/debug/runs/20260525_165725_execute` | `first_wake` | `PASS` | 真机 Cucumber execute 已生成 `AudioInjected`；`WakeDetected_within_3000ms=PASS`，实测 139ms。 |
| `satellite/cucumber-agent-testing/debug/runs/20260525_165940_execute` | `recognition_mode_wake` | `PASS` | 真机 Cucumber execute 中，选中与音频匹配的相邻唤醒簇，间隔 9036ms，AP/CP/ASR 来源齐全。 |

## 2026-05-25 真机全量冒烟

在 `cskwb01` 真机上按 `polaris_voice_core.feature` 将当前 Cucumber 场景全部执行了一次；外部调度 已明确不纳入当前 skill，本轮不覆盖。

汇总文件：

- `satellite/cucumber-agent-testing/debug/runs/true_device_validation_summary_all_20260525.json`
- `satellite/cucumber-agent-testing/debug/runs/remaining_true_device_batch_20260525.json`
- `satellite/cucumber-agent-testing/debug/runs/runtime_profile_full_coverage_20260525.json`

| 场景 | BDD | Runtime | run |
| --- | --- | --- | --- |
| `first_wake` | `PASS` | `PASS` | `20260525_165725_execute` |
| `recognition_mode_wake` | `PASS` | `PASS` | `20260525_165940_execute` |
| `interrupt_prerequisite_measurement` | `PASS` | `PASS` | `20260525_194253_execute` |
| `wake_interrupt` | `PASS` | `PASS` | `20260525_194530_execute` |
| `command_interrupt` | `PASS` | `PASS` | `20260525_194734_execute` |
| `network_recovery_basic` | `PASS` | `PASS` | `20260525_194959_execute` |
| `half_duplex_recognition` | `PASS` | `PASS` | `20260525_195640_execute` |
| `full_duplex_recognition` | `PASS` | `PASS` | `20260525_200031_execute` |
| `basic_command_recognition` | `PASS` | `PASS` | `20260525_200711_execute` |
| `requirement_command_smoke` | `PASS` | `PASS` | `20260525_200750_execute` |
| `requirement_free_speech_smoke` | `PASS` | `PASS` | `20260525_200828_execute` |
| `offline_oneshot_matrix` | `PASS` | `PASS` | `20260525_200909_execute` |
| `online_oneshot_matrix` | `PASS` | `PASS` | `20260525_201002_execute` |
| `wake_latency_smoke` | `PASS` | `PASS` | `20260525_201056_execute` |
| `continuous_wake_smoke` | `PASS` | `PASS` | `20260525_201212_execute` |
| `random_interval_wake_smoke` | `PASS` | `PASS` | `20260525_201233_execute` |
| `online_vad_special_smoke` | `PASS` | `PASS` | `20260525_201340_execute` |
| `false_wake_quiet_basic` | `PASS` | `PASS` | `20260525_201601_execute` |
| `false_wake_human_speech_smoke` | `PASS` | `PASS` | `20260525_201634_execute` |
| `false_wake_white_noise_smoke` | `PASS` | `PASS` | `20260525_201704_execute` |
| `attribution_validator_smoke` | `PASS` | `PASS` | `20260525_201737_execute` |

说明：

- `Runtime=PASS` 表示该场景已经有事件回放旁路断言；截至本次补齐，21 个 Cucumber 场景均已覆盖 Runtime 旁路。
- `half_duplex_recognition` 和 `full_duplex_recognition` 已有独立 Runtime profile，会读取 `judge.json` 里的云端配置应用、半/全双工 timeout 刷新值和成功响应证据。

## 当前边界

- 当前是 Runtime MVP，不是完整 IR/Scene Engine。
- 离线历史日志通常没有准确的 `AudioInjected` 时间，所以 `event_within_ms` 会 `SKIP`；后续接入真机 runner 时由播放动作直接写入 `AudioInjected`。
- `CommandDetected` 当前基于 CP `WAKE(0)`、online ASR 文本、cloud ASR ack、algo keyword 等 marker，后续需要随着项目日志补充。
- `NetworkLost` 目前会解析 WS63 反复 `WiFi disconnected`，这是事件事实；是否影响某条用例由 profile/场景断言决定。
- `MediaCompleted` 会记录播放完成事件，但播放器内部 reset 不会被当作 `CrashDetected`。
- `recognition_mode_wake` 允许一个 `AudioInjected` 对应复合音频中的多次唤醒；断言基于相邻物理唤醒簇，而不是强制每次唤醒都有独立 `AudioInjected`。
- `wake_interrupt/command_interrupt` 优先读取 `interrupt_injection_result.json` 中的计划注入点和自播窗口；如果注入未落入窗口，只能说明时序未命中，不把缺少 wake/ASR 直接归固件。

## Cucumber 旁路接入

当前 `run_cucumber.py` 已把 Runtime replay 作为 execute/summarize 的旁路证据接入：

- 支持当前 `polaris_voice_core.feature` 的 21 个场景；半/全双工已从 `basic_command` 复用升级为独立双工 profile。
- 每次生成 `bdd_run_summary.json` 时，会额外生成 `runtime_replay_summary.json`。
- 每个已支持场景会在 `run_dir/runtime_replay/<scenario_id>/` 下生成：
  - `events.json`
  - `timeline.json`
  - `runtime_state.json`
  - `assertions.json`
  - `replay_package.json`
  - `runtime_replay_report.md`
- `bdd_run_summary.json` 的 `scenario_results[].runtime_replay` 会记录 runtime 结果、事件数量、事件分布和 replay 报告路径。
- `bdd_run_report.md` 的场景结论表会新增 Runtime 列。

重要约束：Runtime 旁路结果暂不改写原 Cucumber 结果。也就是说，原有 summary 判定仍然按现有 probe/fa2/doc runner 规则输出；Runtime 结果先作为更细的事件证据和脚本复核依据。后续等 profile 稳定后，再逐步把部分断言切到 Runtime。

可以对已有 run 目录重新生成旁路证据：

```powershell
python satellite\\cucumber-agent-testing\\scripts\\run_cucumber.py --summarize-run satellite\\cucumber-agent-testing\\debug\\runs\\<run_dir>
```

如果要把 Runtime 非 PASS 结果升级为主判定，可显式打开严格模式：

```powershell
python satellite\\cucumber-agent-testing\\scripts\\run_cucumber.py `
  --summarize-run satellite\\cucumber-agent-testing\\debug\\runs\\<run_dir> `
  --runtime-strict
```

严格模式会保留原始 BDD 结果到 `bdd_result_without_runtime`；当 Runtime 输出 `FAIL`、`ERROR`、`BLOCKED` 或 `TIMING_AMBIGUOUS` 时，最终场景结果会按 Runtime 结果升级。默认不打开严格模式，避免新 profile 尚未稳定时影响历史 BDD 结果。2026-05-26 已用 `20260525_165725_execute` 真机 PASS run 验证：打开 `--runtime-strict` 后 `runtime_strict=true`，场景仍保持 PASS。

## 在线混合压测工程化

夜间在线随机交互压测已从临时 debug 脚本迁移为正式 runner：

```powershell
python satellite\\cucumber-agent-testing\\scripts\\run_task.py `
  --task satellite\\cucumber-agent-testing\\tasks\\examples\\online_mixed_stress.example.json `
  --print-command
```

正式入口与策略文件：

- `satellite/cucumber-agent-testing/scripts/run_online_mixed_stress.py`：正式压测 runner。
- `satellite/cucumber-agent-testing/scripts/analyze_online_stress.py`：压测异常归因分析。
- `satellite/cucumber-agent-testing/references/scene_strategy_pool.json`：在线混合场景权重、语料、间隔和媒体错误 marker。
- `satellite/cucumber-agent-testing/tasks/examples/online_mixed_stress.example.json`：WB01 示例任务。
- `satellite/cucumber-agent-testing/tasks/examples/online_mixed_stress.ws63.example.json`：WS63/AP+WiFi 示例任务。

runner 从 `polaris.local.json` 合并项目配置，WB01 会读取 AP/CP/ASR/control 四口，WS63 会读取 AP/upper/control 且跳过空 CP。示例任务默认 `max_rounds=10`，用于冒烟；夜间压测时把 `max_rounds` 改为 `0` 并填写 `end_at`。

2026-05-25 夜间 WB01 压测结果：

- 总轮次 737：674 PASS，11 `FAIL_NO_WAKE`，51 `WARN_MEDIA_ERROR`，1 `WARN_NO_ASR`。
- 无 reboot/crash，无串口 reader 异常。
- 异常归因脚本输出：51 个 `network_or_online_media`、10 个 `wake_not_detected_after_successful_playback`、1 个 `self_play_overlap_or_device_busy`、1 个 `partial_wake_no_asr`。
- 归因报告位于 `satellite/cucumber-agent-testing/debug/wb01_online_stress/20260525_overnight_runtime_closure/analysis/`，属于运行产物，不提交仓库。

## 媒体校验边界

当前媒体/TTS/MP3/相声/新闻类在线响应使用日志证据校验，主要包括：

- 在线 ASR、云端回复或 NLU 命中。
- AP/upper 日志里的 `audioBroadcast`、TTS URL、stream TTS、player play/stop/complete。
- HTTP、demux、download、player 相关 error/timeout/fail marker。
- 同一窗口内的 reboot/crash/串口异常。

因此当前能证明“设备日志侧是否进入媒体播放链路、是否出现媒体错误”，但在没有声卡回采或麦克风回录前，不能证明扬声器真实出声质量、音量大小、播报内容完整性或声学断续。出现 HTTP/media error 且已有播放链路证据时优先输出 WARN；完全没有播放证据时才按媒体链路失败继续归因。后续如要把“真实出声”纳入主断言，需要增加回采声卡、声学 VAD/关键词检测或设备端更明确的播放完成 oracle。

## 误唤醒/误识别记录

测试窗口内除了期望的唤醒词和命令词，还必须保留所有额外识别证据。当前 Runtime replay 会在 `assertions.json` 中输出 `recognition_observations`：

- `wake_event_count`、`asr_event_count`、`command_event_count`：本窗口内所有唤醒、ASR、命令事件数量。
- `recognized_texts`：在线/离线 ASR、云端 ASR ack 等识别文本。
- `recognized_commands`：CP `WAKE(0)`、AP algo keyword、local ASR keyword 等命令关键词。
- `wake_keywords`：日志里能提取到的唤醒 keyword。
- `observations[]`：每条识别证据的 event_id、时间、来源、文件、行号和原始片段。

断言口径：

- 误唤醒/静默/干扰场景：不允许出现 wake、ASR 或 command；只要出现任一类识别事件，Runtime 判 FAIL，并保留原始行。
- 命令词/one-shot/VAD/在线交互场景：记录实际 ASR 文本和命令关键词；如果和本轮语料不一致，先按误识别/串音/上轮自播残留/文本归一化缺失复核，不能只因为“有 ASR”就判 PASS。
- 在线混合压测：`rounds.csv` 和逐轮 `result.json` 会写 `expected_utterances`、`asr_texts`、`command_keywords`、`unexpected_asr_texts`；出现不匹配 ASR 时输出 `WARN_UNEXPECTED_RECOGNITION`。

## 下一步

推荐下一步按这个顺序继续：

1. 抽样复跑 10 个 `wake_not_detected_after_successful_playback` 轮次，确认是偶发唤醒、声卡/PA/麦克风链路，还是自播残留占用。
2. 继续把更多 runner 的播放动作统一写入 `*_runtime_events.jsonl`，减少历史日志 `SKIP`。
3. 增加声卡回采或设备端播放完成 oracle，把媒体“真实出声”从日志 proxy 升级为可断言证据。
