# Polaris Cucumber Agent Testing

这个目录把 Polaris 语音功能测试沉淀成 **Cucumber/BDD + 本地 Agent runner**：

- Feature 用自然语言描述“前置、动作、证据、断言”。
- `references/voice_core_mapping.json` 和 registry 固化每个功能怎么执行、怎么断言、怎么归因。
- 执行时不依赖大模型、不依赖网络生成脚本；clone 仓库后按配置文件即可运行。
- 默认 `plan-only` / `dry-run` 不占用串口、不播放音频；真机执行必须显式允许 side effects。

完整测试项、断言口径、Cucumber 用例写法和方案读取流程见 `../../docs/skill/supported-test-items-cucumber-guide.md`。

## 30 秒上手

在仓库根目录执行：

```powershell
# 1. 准备本机配置。首次 clone 后复制根目录模板，再按自己的设备改参数。
Copy-Item polaris.local.example.json polaris.local.json
notepad polaris.local.json

# 2. 先 dry-run，确认会触发哪个场景、哪些脚本、哪些断言。
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json

# 3. 真机执行。会占用串口、播放声卡、可能调用网络/云端/上下电能力。
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json --mode execute --allow-side-effects --manage-session
```

默认配置查找顺序：命令行 `--env-file` > 任务文件 `environment.env_file` > 根目录 `polaris.local.json` > 旧版 `config/polaris_env.json`。

输出统一写到：

```text
satellite/cucumber-agent-testing/debug/runs/<时间戳>_<模式>/
```

关键文件：

- `execution_plan.md`：本次要执行什么。
- `run_summary.json`：runner 原始结果。
- `bdd_run_report.md`：BDD 汇总报告，execute 模式生成。
- `logs/`、`session/`：串口、播放、模块脚本证据。

## 按项目选择配置

新入口统一使用根目录 `polaris.local.json`，按 `active_project` 选择当前项目，不再要求新人进入 `config/` 目录找串口配置。

```json
{
  "active_project": "venusws63",
  "projects": {
    "cskwb01": { "serial": { "ports": { "ap": "COM13", "cp": "COM14", "asr": "COM11", "control": "COM12" } } },
    "venusws63": { "serial": { "ports": { "ap": "COM20", "upper": "COM17", "asr": "COM17", "cp": "", "control": "COM19" } } }
  }
}
```

WB01 项目至少改：

- `active_project="cskwb01"`。
- `projects.cskwb01.serial.ports.ap/cp/asr/control`：AP、CP、WB01/ASR、控制口。
- `projects.cskwb01.serial.baudrate`：当前数据口为 `921600`，控制口 `115200`；以设备实际为准。
- `common.audio.default_playback_device_key`：声卡稳定 key；留空或未配置时使用电脑默认播放声卡。
- `common.device.wake_word/wakeup_id`：唤醒词和日志里的 wakeup id。
- `projects.cskwb01.network.wifi_ssid/wifi_password/enable_hotspot_control`：联网/断网类用例需要。
- `projects.cskwb01.cloud.api_environment/device_env/device_env_command`：API 调试环境和设备端切换命令。
- `common.device.iot_id/projects.cskwb01.cloud.device_id`：默认可留空；只有 API/云控无法通过 `deviceinfo` 自动读取时才手动填写。
- `projects.cskwb01.serial.control_preconditions`：声卡播放成功但设备不唤醒时的 PA 前置，例如 `uut-pa.on`、`pa-enable.set 0 17 0 1`，必须发到控制口。

WS63 项目至少改：

- `active_project="venusws63"`。
- `projects.venusws63.serial.ports.ap/upper/control`：AP、上位/WiFi、控制口。
- `projects.venusws63.serial.ports.cp`：保持空字符串，表示无 CP。
- `projects.venusws63.serial.baudrate`：AP/上位日志口波特率，例如 `921600`。
- `projects.venusws63.serial.control_baudrate`：控制口波特率，例如 `115200`。
- `projects.venusws63.serial.control_preconditions`：PA/声卡链路前置命令，例如 `uut-pa.on`、`pa-enable.set 0 17 0 1`。
- `common.audio.default_playback_device_key`、`common.device.wake_word/wakeup_id`；没有单独声卡时 `default_playback_device_key` 可留空。
- `projects.venusws63.cloud.api_environment/device_env/device_env_command`；如设备不连本机 Wi-Fi，`network.wifi_ssid` 可留空，断网/联网用例会被阻塞或跳过。
- `common.device.iot_id/projects.venusws63.cloud.device_id`：默认可留空；只有 API/云控无法通过 `deviceinfo` 自动读取时才手动填写。

2026-06-01 当前 WS63 已验证配置：AP `COM20@921600`、upper/asr `COM17@921600`、control `COM19@115200`、声卡 `VID_8765&PID_5678:9_27F546DA_3_0000`、env=`1/UAT`。云控 API 前必须先 `check-env`，外部 Wi-Fi 不可控时断网/恢复用例按 `BLOCKED_NETWORK_NOT_CONTROLLABLE` 留证。

### 声卡 key 查询与 laid 安装

新电脑首次运行时先执行：

```powershell
python tools\audio\polaris_laid.py ensure
python tools\audio\polaris_laid.py list --direction Render
```

`ensure` 会先检查当前用户 shell 里是否已有 `laid` 命令；没有时从 `tools/audio/laid/` 下的自带安装脚本安装。Windows 写入当前用户 PowerShell profile，Linux 写入 `~/.bashrc`/`~/.zshrc`。查询结果里复制 `Render` 行的 `DeviceKey` 到 `common.audio.default_playback_device_key`；如果没有项目专用声卡，可留空使用电脑默认播放设备。

`config/polaris_env.json` 与 `config/polaris_local_ports.json` 只作为旧脚本兼容/缓存，不再作为新人主配置入口。

## API 环境必须和设备端一致

凡是 `set-volume`、`set-full-duplex`、`set-night-mode`、`set-mic`、`set-wakeup-threshold`、主动播报等 API/云控类验证，必须先确认设备端 CSK/AP 已切到对应调试环境：

| 目标环境 | CSK/AP 串口命令 | 重启 | API 参数 |
| --- | --- | --- | --- |
| UAT | `flash.set.int env@1` | `reboot` | `cloud.api_environment=uat` |
| SIT | `flash.set.int env@2` | `reboot` | `cloud.api_environment=sit` |
| PRO | `flash.set.int env@0` | `reboot` | `cloud.api_environment=pro` |

`cloud.device_env`、`cloud.device_env_command` 和 `cloud.api_environment` 要保持一致。只改 API 参数、不切设备端环境，常见结果是 API 返回成功但设备不生效，或出现 connector/channel 异常。

云控 API 有强制环境门禁：工具必须先从 AP/CSK 串口实际解析到设备端 `env` 与 `cloud.api_environment` 一致，才能继续调用网络云端配置 API。查询失败、解析不到 env、设备还在 PRO/env=0 而 API 配置为 UAT/SIT、或只执行了切换命令但未重启/复查成功，都必须直接 `BLOCKED`，不能继续调用 `set-full-duplex`、`set-volume`、`set-mic`、`set-night-mode`、`set-wakeup-threshold`、主动播报等配置接口。

推荐顺序固定为：

```text
version -> flash.show/flash.get.int env -> 必要时 flash.set.int env@N -> reboot -> 等待联网
  -> 复查 env=N -> deviceinfo 确认 IoT ID -> 调云控 API -> 检查 HTTP/业务码/设备 marker
```

## 两种触发方式

### 方式 A：按 tag 直接触发

适合熟悉框架的人：

```powershell
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode dry-run --tag first_wake
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag first_wake --allow-side-effects --manage-session
```

### 方式 B：按任务配置触发（推荐给新人）

适合开源仓库 clone 后快速上手：

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\basic_command.example.json
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\basic_command.example.json --mode execute --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_full_duplex.example.json --mode dry-run
```

任务文件只描述“我要测哪个功能、用哪些输入、执行窗口多长、是否管理串口 session”。本机串口、声卡、Wi-Fi 等硬件差异放到 `polaris.local.json`。

## 必配参数

首次 clone 后至少检查这些参数。表格中的路径是“生效后的字段”，在根配置里通常位于 `common.*` 或 `projects.<active_project>.*`：

| 配置路径 | 必填 | 含义 | 示例 |
| --- | --- | --- | --- |
| `serial.ports.ap` | 是 | AP/cskap 日志串口 | `COM14` |
| `serial.ports.cp` | 是 | CP/cskcp 日志串口 | `COM12` |
| `serial.ports.asr` | 是 | ASR/WB01 日志串口 | `COM13` |
| `serial.ports.control` | 按需 | 上下电/复位控制串口 | `COM15` |
| `serial.baudrate` | 是 | 串口波特率 | `115200` |
| `audio.default_playback_device_key` | 建议 | 播放唤醒词/命令词的声卡稳定 key；留空时使用电脑默认声卡 | `VID_8765&PID_5678:9_2A847557_7_0000` |
| `device.wake_word` | 是 | 当前唤醒词中文文本 | `小美小美` |
| `device.wakeup_id` | 建议 | 设备日志里的唤醒 ID | `xiao mei xiao mei` |
| `network.wifi_ssid` | 联网场景必填 | 测试热点/路由 SSID | `<wifi_ssid>` |
| `network.wifi_password` | 联网场景必填 | Wi-Fi 密码 | `<wifi_password>` |
| `cloud.api_environment` | API 场景必填 | 云端 API 环境，必须匹配设备端环境 | `uat` / `sit` |
| `cloud.device_env_command` | API 场景必填 | 切换设备端 CSK/AP 环境的串口命令 | `flash.set.int env@1` |
| `cloud.device_id` | API 场景按需 | API 使用的 deviceId/IoT ID；能自动 `deviceinfo` 时可留空 | `210006741088068` |
| `paths.command_file` | 命令词场景必填 | 命令词文件路径 | `docs/fa2命令词.txt` |
| `timeouts.observe_ms` | 是 | 每轮触发后观察串口窗口 | `15000` |
| `timeouts.recognition_timeout_s` | 识别模式相关 | 识别模式超时时间 | `15` |
| `timeouts.half_duplex_timeout_s` | 半双工相关 | 半双工窗口/超时口径 | `15` |
| `timeouts.full_duplex_timeout_s` | 全双工相关 | 全双工窗口/超时口径 | `60` |
| `timeouts.timing_guard_ms` | 边界时序建议 | 避开唤醒播报和超时临界点的保护时间 | `1200` |
| `timeouts.wake_cluster_gap_ms` | Runtime 唤醒聚类 | 多端 wake marker 合并为一次物理唤醒的最大间隔 | `2500` |
| `timeouts.interrupt_guard_ms` | 打断相关 | 注入点距离自播 start/end 的保护时间 | `600` |
| `timeouts.post_injection_ms` | 打断相关 | 注入后观察 wake/ASR/命令证据的窗口 | `5000` |
| `timeouts.post_recovery_ms` | 联网恢复相关 | 恢复在线后观察在线语音闭环的窗口 | `60000` |

更详细说明见 `docs/configuration.md`。

## 任务配置长什么样

最小任务配置：

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
    "allow_side_effects": false,
    "adapter_flows": {
      "pre": [
        {"flow": "pa_recover", "when": "dry-run", "required": false}
      ]
    }
  }
}
```

`execution.adapter_flows.pre/post` 会由 `run_optimized_task.py` 在主流程前/后调用 `plan_adapter_flow.py`。默认 dry-run 只渲染命令；`mode=execute` 且显式 `--allow-side-effects` 时才会真实执行。`required=true` 的 pre flow 失败会阻断主流程，避免前置环境没准备好时误判固件失败。

如果是命令词识别，再加输入：

```json
{
  "inputs": {
    "command_file": "docs/fa2命令词.txt",
    "command_limit": 20
  }
}
```

新功能第一次接入，请从 `tasks/templates/new_feature.template.json` 复制，先补齐功能意图、前置、动作、期望证据和失败归因，再沉淀到正式 feature/mapping/registry。

## 在线混合压测

在线混合压测已从临时 debug 脚本迁移到正式入口：

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py `
  --task satellite\cucumber-agent-testing\tasks\examples\online_mixed_stress.example.json `
  --print-command
```

确认命令无误后去掉 `--print-command` 执行。该任务会占用串口、声卡和云端链路，任务文件中必须显式设置 `allow_side_effects=true`。

示例任务默认只跑 10 轮，适合新环境冒烟；需要整晚压测时，把任务文件里的 `execution.max_rounds` 改为 `0`，并填写未来的 `execution.end_at`，例如 `2026-05-27T08:30:00`。WB01 使用 `tasks/examples/online_mixed_stress.example.json`；WS63/AP+WiFi 使用 `tasks/examples/online_mixed_stress.ws63.example.json`，或把任意任务中的 `environment.project` 改成 `venusws63`。

相关文件：

- `scripts/run_online_mixed_stress.py`：正式压测 runner。
- `scripts/analyze_online_stress.py`：压测异常归因分析。
- `references/scene_strategy_pool.json`：加权随机策略和语料池。
- `tasks/examples/online_mixed_stress.example.json`：可复制任务配置。
- `tasks/examples/online_mixed_stress.ws63.example.json`：WS63 无 CP 项目的可复制任务配置。

执行产物默认写入 `satellite/cucumber-agent-testing/debug/online_mixed_stress/<timestamp>/`，包括完整串口日志、`rounds.csv`、逐轮 `result.json`、实时心跳和最终报告。运行结束后可执行：

```powershell
python satellite\cucumber-agent-testing\scripts\analyze_online_stress.py `
  --run-dir satellite\cucumber-agent-testing\debug\online_mixed_stress\<timestamp>
```

当前媒体响应校验是“日志证据优先”：runner 会同时看在线 ASR/云端回复、`audioBroadcast`、TTS URL、player play/stop/complete、HTTP/media error、reboot/crash。能证明设备进入播放链路但出现 HTTP/媒体错误时标记 `WARN_MEDIA_ERROR`，完全没有播放证据才按媒体链路失败继续归因。没有接入声卡回采/麦克风回录前，框架不能证明扬声器真实出声质量，只能证明设备日志侧已经或未进入媒体播放链路。

压测和 Runtime 都会记录“额外识别结果”：包括窗口内出现的 wake marker、在线/离线 ASR 文本、CP `WAKE(0)` 关键词、AP algo keyword。在线压测会把这些写入 `rounds.csv` 和逐轮 `result.json` 的 `asr_texts`、`command_keywords`、`expected_utterances`、`unexpected_asr_texts`；如果 ASR 文本与本轮播放语料不匹配，会输出 `WARN_UNEXPECTED_RECOGNITION`，按误识别/串音/上轮自播残留复核。误唤醒场景中任何 wake、ASR 或 command 事件都会作为 Runtime FAIL，而不是只看 wake marker。

## 在线全双工验证

在线全双工已沉淀为可复制任务：`tasks/examples/online_full_duplex.example.json`。建议先做 precheck/dry-run：

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_full_duplex.example.json --precheck-only
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_full_duplex.example.json --mode dry-run
```

真机执行时追加副作用确认：

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_full_duplex.example.json --mode execute --allow-side-effects --manage-session --runtime-strict
```

该任务的 pre adapter flow 会检查 `laid`、切设备端 UAT/SIT 环境、确认在线、下发全双工；主流程执行 `@full_duplex_recognition`，Runtime 会检查全双工配置、timeout 刷新、唤醒前因、ASR/响应闭环以及 reboot/crash。方案和用例矩阵见 `../../docs/wiki/voice-validation/packs/online-full-duplex.md`，运行期归因见 `../../docs/knowledge/common/online_full_duplex_validation.md`。

在线全双工完整矩阵 FD-002~FD-012 已拆成独立 task 和 scene，可先做 scene 级 dry-run：

```powershell
python satellite\cucumber-agent-testing\scripts\run_kernel_scene.py --scene satellite\cucumber-agent-testing\references\scenes\online_full_duplex_fd002_fd012.scene.example.json --mode dry-run --execute-runner --emit-ir-bundle
```

单项 task 位于 `tasks/examples/online_full_duplex.*.example.json`，分别覆盖连续对话、媒体打断、超时边界、异常矩阵和随机稳定性。

## 已支持 tag

| tag | 功能 |
| --- | --- |
| `first_wake` | 首次唤醒 |
| `recognition_mode_wake` | 识别模式下唤醒 |
| `half_duplex_recognition` | 半双工识别 |
| `full_duplex_recognition` | 全双工识别 |
| `basic_command_recognition` | 基础命令词识别 |
| `requirement_command_smoke` | 需求命令词小样本 |
| `requirement_free_speech_smoke` | 需求自由说小样本 |
| `interrupt_prerequisite_measurement` | 打断前置自播测量 |
| `wake_interrupt` | 自播中唤醒打断 |
| `command_interrupt` | 自播中识别打断 |
| `network_recovery_basic` | 联网恢复基础验证 |
| `offline_oneshot_matrix` | 离线 one-shot 间隔矩阵 |
| `online_oneshot_matrix` | 在线 one-shot 间隔矩阵 |
| `false_wake_quiet_basic` | 静默误唤醒监听 |
| `wake_latency_smoke` | 唤醒响应时间小样本 |
| `continuous_wake_smoke` | 连续唤醒稳定性小样本 |
| `random_interval_wake_smoke` | 随机间隔唤醒小样本 |
| `online_vad_special_smoke` | 在线 VAD 专项小样本 |
| `attribution_validator_smoke` | 归因一致性复核 |
| `false_wake_human_speech_smoke` | 合成人声干扰误唤醒 |
| `false_wake_white_noise_smoke` | 白噪声误唤醒 |

截至 2026-05-25，以上 21 个 tag 均已接入 Event Runtime 旁路 replay；`bdd_run_summary.json` 会同时输出 BDD 结果和 `runtime_replay` 事件断言结果。半/全双工已接入独立 profile，会读取 `judge.json` 中的云端配置应用、timeout 刷新值和成功响应证据。

默认情况下 Runtime 只作为旁路证据，不改写 BDD 主结果。需要把 Runtime 非 PASS 结果升级为主判定时，增加：

```powershell
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --summarize-run <run_dir> --runtime-strict
```

或在 task 的 `execution.runtime_strict=true` 后通过 `run_task.py` 执行。建议先用于回放/复核，确认 profile 稳定后再用于正式门禁。

`--runtime-strict` 的含义是：BDD 原结果仍会保留到 `bdd_result_without_runtime`；如果 Runtime profile 输出 `FAIL`/`ERROR`/`BLOCKED`/`TIMING_AMBIGUOUS`，最终场景结果会按 Runtime 升级后的结果输出。当前已用真机 PASS run 验证该开关不会导致 PASS 场景回归；建议先在 summarize/replay 阶段启用，再逐步用于正式执行。

首唤醒 profile 已处理声卡播放工具初始化耗时：如果播放进程明显长于 wav 时长，Runtime 会用 `AudioCompleted - audio_duration_ms` 估算有效波形起点，再计算 `WakeDetected_within_3000ms`；仍无法估算时才输出 `TIMING_AMBIGUOUS`。WS63 `cp` 留空时，BDD/Runtime 都只要求 AP/ASR 唤醒闭环。

当前本地 Runtime 还提供 adapter registry、capability matrix、event graph、state assertion DSL、Validation IR、Validation Kernel 生命周期、Kernel scene 调度和 analytics trend 入口，分别用于回答“这个项目能测什么、要占用什么资源、事件因果链是什么、最终状态是否安全、task/env/scene/feature plan 如何进入统一 IR、一次执行经历了哪些 kernel 阶段、scene 每个节点是否独立闭环、历史结果趋势如何”。adapter 单动作可通过 `run_adapter_action.py` 先 dry-run 渲染命令，真执行副作用必须显式 `--execute --allow-side-effects`。

`compile_validation_ir.py` 支持三种入口：`--task` 输出单个 `polaris.validation_ir.v1`，`--scene` 输出 scene 级 `polaris.validation_ir_bundle.v1`，`--feature-plan` 可把 `compile_feature.py` 生成的 `compiled_plan.json` 转成 feature 级 IR bundle。`run_kernel_scene.py --emit-ir-bundle` 可在调度前输出 `scene_validation_ir_bundle.json`，用于验证 scene node 是否已经落到同一套 IR 字段：`intent/preconditions/actions/expect/timeout/retry/cleanup/metadata`。

`runtime_state.json` 已包含 `state_health`、`state_violations`、`coverage` 和 `transitions`。后续判断重启/崩溃、识别前因缺失、音频或媒体证据顺序不完整时，优先引用这些字段，不要只看最终场景 PASS/FAIL。

`run_state_coverage_policy.py` 会把 `runtime_state.coverage` 按 profile 阈值转成 PASS/WARN/FAIL，例如首唤醒要求 WakeDetected 且禁止 Crash/Reboot，基础命令要求 ASRDetected 或 CommandDetected，联网恢复要求 NetworkLost 和 NetworkRecovered。Validation Kernel 后处理会把该结果写入 `state_coverage_policy.json` 并汇总到 `runtime_analysis.json`。

项目差异不要写死到代码里；在 `state_assertion_policy.json` 的 `coverage.projects.<project_id>` 下添加 `common` 或 `profiles.<profile>` 覆盖即可。例如 WS63 不要求 CP 闭环、某项目半双工窗口更短，都应先落到项目覆盖策略。

`run_assertion_dsl.py` 已支持基础时序和业务链路 DSL：`EXPECT`、`FORBID`、`EXPECT_SEQUENCE`、`EXPECT_RESPONSE`、`EXPECT_DURATION`。在线问答、音乐、新闻、相声等场景可先用它验证“识别后是否出现 TTS/Media 响应、响应是否在指定窗口内、播放持续是否达到阈值”，后续再把稳定规则沉淀进 profile 断言。

`build_capability_matrix.py` 已把常见缺口拆细：音频回采 oracle、媒体日志响应 oracle、真实声学响应 oracle、云控权限、boot reason oracle。新项目只配串口和 Wi-Fi 后，先跑能力矩阵，就能看到哪些测试可直接执行、哪些需要补资料或补硬件。

`build_event_graph.py` 已输出 `risk_summary`，并补充媒体、云端响应、打断、重启/崩溃因果边。在线压测后分析异常时，优先看 `possible_reboot_after_activity`、`possible_crash_after_activity`、`media_interrupted`、`interrupt_to_recognition`、`media_started_to_completed` 等关系。

如果某个项目的云端、音乐、新闻、相声、TTS 或 MP3 marker 比较特殊，不要直接改核心代码；先把规则写到 `references/optimization/event_graph_rules.json`，或用 `build_event_graph.py --rules <file>` 指定 overlay。规则命中后会在 `risk_summary.rule_overlay` 和 `relation_counts` 中体现。

`run_adapter_action.py` 默认 dry-run，不会真实占用串口/声卡/云端。当前 registry 已覆盖控制口 PA/上下电、AP 设备环境切换、声卡播放、热点状态/恢复、常用云控 API 设置；真执行必须显式追加 `--execute --allow-side-effects`。

`plan_adapter_flow.py` 把常见前置/调试流程固定映射到 Adapter Executor，例如 `pa_recover`、`power_on`、`switch_device_env`、`ensure_online`、`wake_audio_file`、`set_volume`、`set_half_duplex`、`set_full_duplex`。它默认只渲染命令，真执行同样必须追加 `--execute --allow-side-effects`。

Runtime replay 的 `assertions.json` 和 `runtime_replay_report.md` 还会输出 `recognition_observations`，用于追溯“设备到底识别了什么”。如果本轮没有播放某个词，但该字段里出现了对应 wake/ASR/command 结果，就不能简单当作 PASS 旁证，需要按误唤醒或误识别归因。

## 新功能怎么接入

1. 在 `features/polaris_voice_core.feature` 写场景和 tag。
2. 在 `references/voice_core_mapping.json` 或 `step/action/assertion registry` 中注册执行逻辑。
3. 明确断言：哪些证据算 PASS，哪些算 FAIL/BLOCKED/TIMING_AMBIGUOUS/REQUIREMENT_REVIEW。
4. 在 `tasks/examples/` 增加一个可复制任务文件。
5. 先跑 `dry-run`，再真机小样本，最后再做压测或全量。

原则：自然语言用例可以变，但只要落在已注册的功能意图和 step/action/assertion 上，脚本就不需要大模型实时改代码。

如果新功能来自新的项目资料、外部测试方案或类似 `voice-test-plan-designer` 的 skill，先不要直接改本目录。先把资料放到根目录 `docs/intake/<project_id>/<YYYYMMDD_topic>/`，填写 `learning_manifest.json`。学习和缺口分析完成后，通用方法先沉淀到 `../../docs/wiki/`，项目差异沉淀到 `../../docs/knowledge/<project_id>/`，再把可执行部分沉淀到本目录的 feature/reference/task/runtime。

