# Polaris Voice Validation Skill

Polaris 是一个面向嵌入式语音设备的本地真机验证 skill。当前仓库已经切换到新的 **Cucumber/BDD + Event Runtime** 方案：用 Cucumber 描述测试意图，用固定 runner 执行动作，用 Event Runtime 把串口、声卡、云控和执行产物统一转换成事件，再由确定性的断言逻辑判断 PASS/FAIL/BLOCKED。

本仓库不要求每次执行时联网或依赖大模型生成脚本。新用例只要进入已有的 step/action/assertion registry，后续就可以脱离大模型稳定执行。

## 0. 一页读懂：这个 skill 是什么、怎么用、亮点在哪里

### 0.1 一句话定位

Polaris 是一套“语音设备真机自动化验证框架”：把用户的自然语言测试需求沉淀成 BDD/Cucumber 用例，再通过固定 Adapter、串口/声卡/云控工具、Event Runtime 和断言引擎完成真机功能验证、长时间稳定性压测、异常归因和报告输出。

它不是一次性调试脚本集合，而是一个可以持续学习新项目、新功能、新日志规则的本地 skill。

### 0.2 支持的两类核心任务

| 类型 | 解决的问题 | 典型场景 | 典型结论 |
|---|---|---|---|
| 功能测试验证 | 某个功能是否符合需求 | 首次唤醒、识别模式唤醒、半/全双工、基础命令词、在线问答、打断、one-shot、联网恢复 | `PASS / FAIL / BLOCKED / WARN / TIMING_AMBIGUOUS`，并给出失败归因 |
| 稳定性压测 | 长时间随机交互是否稳定 | 一晚在线混合压测、唤醒率压测、随机间隔命令词/媒体交互、重启/崩溃监控 | 总轮数、异常轮次、重启/crash/watchdog、无唤醒、无 ASR、媒体错误、误识别候选 |

### 0.3 你如何触发一次测试

#### 方式 A：你给一句需求，我生成方案和用例，确认后执行

适合功能验证，例如“测试在线全双工相关功能”“测试唤醒功能”“验证打开空调控制链路”。

```text
用户需求
  -> 需求解读
  -> 生成测试方案、用例矩阵、关注点、缺口
  -> 用户确认
  -> Cucumber/Task 真机执行
  -> Runtime replay + 断言
  -> 总报告和问题归因
```

生成确认包：

```powershell
python satellite\cucumber-agent-testing\scripts\generate_requirement_package.py --requirement "在线全双工相关功能验证" --project cskwb01 --env-file polaris.local.json
```

#### 方式 B：直接运行已有标准任务

适合已有能力的快速验证，例如首次唤醒、基础命令词、在线混合压测。

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py `
  --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json `
  --mode execute --allow-side-effects --manage-session --runtime-strict
```

#### 方式 C：运行稳定性压测

适合夜间长时间压测，例如在线音乐/新闻/相声/问答/命令词随机混合。

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py `
  --task satellite\cucumber-agent-testing\tasks\examples\online_mixed_stress.example.json `
  --mode execute --allow-side-effects
```

#### 方式 D：对历史日志补抽在线请求 ID

适合根据 `mid/sessionId/recordId` 到云端捞对应音频。

```powershell
python satellite\cucumber-agent-testing\scripts\extract_online_request_ids.py --log <COMxx.log>
```


### 0.3.1 Regression and latency summary

Run the local regression suite to cover parser, snapshot, runtime, report rendering, and latency-summary smoke checks.

```powershell
python satellite\cucumber-agent-testing\scripts\run_regression_suite.py
```

Key artifacts:

- `debug/regression_suite/<stamp>/regression_suite_summary.json`
- `debug/regression_suite/<stamp>/regression_suite_report.md`
- `debug/regression_suite/<stamp>/latency_summary/latency_summary_report.md`

Build a latency summary from existing `online_request_ids.csv/json` artifacts when needed:

```powershell
python satellite\cucumber-agent-testing\scripts\build_latency_summary_report.py `
  --input satellite\cucumber-agent-testing\debug\logic_regression_smoke\20260529_1805 `
  --out-dir satellite\cucumber-agent-testing\debug\latency_summary_manual
```

The report summarizes wake-to-recognition, recognition-to-TTS/media-start, playback duration, P50/P90/P99/TopN, and evidence gaps that require project-specific TTS/media markers.

### 0.4 框架执行逻辑

```text
polaris.local.json 项目配置
        ↓
需求 / Task JSON / Cucumber Feature
        ↓
Step / Action / Assertion Registry
        ↓
Adapter Executor
  - 串口日志
  - 控制口上下电/PA
  - 声卡播放/laid 查询
  - 云控 API/UAT/SIT
  - 网络/热点/媒体辅助
        ↓
真机执行产物 debug/<run>
        ↓
Event Runtime Replay
  - 串口/JSON/播放产物解析为事件
  - Wake/ASR/Command/TTS/Media/Network/Reboot 状态机
  - 时序、覆盖率、禁止事件、稳定性断言
        ↓
报告
  - 功能结论
  - 失败归因
  - 唤醒-识别-云端请求-响应链路
  - 证据目录
```

### 0.4.1 整体框架模块功能介绍

当前框架按优化方案拆成“需求/知识 -> BDD/IR -> Adapter 执行 -> Event Runtime -> 断言/报告 -> 失败反哺”的分层结构。理解这些模块后，就能知道为什么新增需求通常只需要补知识、用例或 registry，而不是每次重新写一套脚本。

```text
需求/资料/项目配置
        ↓
知识库 + BDD/Task/Scene
        ↓
Registry / Validation IR Compiler
        ↓
Validation Kernel / Runner / Scene Engine
        ↓
Device Adapter Layer
        ↓
真机证据采集
        ↓
Event Runtime: Event Bus + Timeline + StateMachine
        ↓
Assertion / Coverage / Event Graph
        ↓
报告输出 + 失败归因 + 回归用例反哺
```

| 模块 | 主要职责 | 主要输入 | 主要输出 | 目录/入口 |
|---|---|---|---|---|
| 配置与项目画像层 | 统一管理当前项目、串口拓扑、声卡、UAT/SIT、Wi-Fi、唤醒词、能力开关；避免把 COM 口和环境写死到脚本里。 | `polaris.local.json`、`polaris.local.example.json`、项目 overlay、能力声明。 | 归一化 env、项目 capability、串口/声卡/云控配置。 | 根目录配置、`satellite/cucumber-agent-testing/scripts/polaris_env.py` |
| 需求理解与知识库层 | 把用户需求、表格、项目资料、历史调试经验转成可复用测试方法、断言规则和项目差异。 | 用户一句需求、`docs/intake/` 原始资料、`docs/requirements/`、旧资料归档。 | 测试方案思路、验证包、gap list、项目私有 rule。 | `docs/wiki/`、`docs/knowledge/<project_id>/`、`docs/intake/` |
| BDD / Task / Scene 用例层 | 表达“要验证什么”，让用例可读、可评审、可组合；不在自然语言里塞复杂时序和断言细节。 | Cucumber feature、task JSON、scene JSON、需求包。 | 可执行任务、场景矩阵、标签、需求映射。 | `features/`、`tasks/`、`tasks/examples/`、`references/scenes/` |
| Registry / IR 编译层 | 把自然语言 step、task、scene 映射成已知 action/assertion；统一编译为 deterministic Validation IR。 | Feature plan、task、scene、`voice_core_mapping.json`、assertion/profile 配置。 | 执行计划、IR bundle、动作/断言清单。 | `scripts/compile_feature.py`、`scripts/compile_validation_ir.py`、`references/voice_core_mapping.json` |
| Adapter 执行动作层 | 只负责把固定动作落到设备：串口写入、声卡播放、PA/上下电、云控、网络辅助；Runtime 不关心具体项目怎么接线。 | IR/action plan、env 配置、adapter flow。 | adapter result、pre/post flow、命令执行证据。 | `tools/core/polaris_adapter_bridge.py`、`scripts/run_adapter_action.py`、`scripts/plan_adapter_flow.py` |
| 真机会话与资源管理层 | 管理串口会话、日志采集、资源占用、side-effect 门禁；串口打不开或关键角色缺失时输出 BLOCKED/覆盖降级。 | `--manage-session`、串口配置、side-effect 许可、资源策略。 | session manifest、live logs、serial coverage、constraint result。 | `tools/core/polaris_runtime.py`、`runtime/resource_runtime.py`、`runtime/constraint_engine.py` |
| 执行编排层 | 负责 dry-run/execute、重试、scene 顺序、kernel 生命周期、前置/收尾动作和 run 目录组织。 | task/scene/IR、运行模式、重试策略、adapter flow。 | execution_record、attempts、run_summary、kernel artifacts。 | `scripts/run_task.py`、`scripts/run_optimized_task.py`、`scripts/run_validation_kernel.py`、`scripts/run_kernel_scene.py` |
| 证据采集层 | 保存 AP/CP/ASR/上位/控制口日志、声卡播放记录、云控响应、媒体/TTS 产物和窗口日志。 | 真机运行过程、串口流、播放/云控/网络工具输出。 | `merged.log`、`COM*.log`、window logs、playback/cloud/media artifacts。 | `satellite/cucumber-agent-testing/debug/<run>/` |
| Event Runtime / 事件解析层 | 把原始日志和产物转换成统一事件，形成时间线和交互链路；后续断言不再直接猜日志文本。 | 串口日志、result JSON、播放产物、云端响应。 | `events.json`、`timeline.json`、interaction trace、wake/asr/media/network/reboot events。 | `runtime/events.py`、`runtime/timeline.py`、`runtime/parsers/`、`tools/logs/polaris_interaction_trace.py` |
| 状态机与断言层 | 用状态机、时序窗口、排斥事件、coverage 阈值判断 PASS/FAIL/BLOCKED/WARN/TIMING_AMBIGUOUS。 | Event timeline、state policy、assertion DSL、项目覆盖阈值。 | `assertions.json`、`runtime_state.json`、state_health、coverage、失败分类。 | `runtime/state_machine.py`、`runtime/assertion_engine.py`、`scripts/run_state_assertion_dsl.py`、`scripts/run_state_coverage_policy.py` |
| Event Graph 与归因层 | 建立事件因果关系，识别 ASR 到 TTS/media/control 的链路断点、重启/crash 风险和项目私有 marker。 | events/timeline、event graph rules、项目 overlay。 | risk_summary、因果边、失败归因线索。 | `scripts/build_event_graph.py`、`references/optimization/event_graph_rules.json`、`references/project_marker_overlays.json` |
| 失败反哺与回归层 | 把真机失败转成候选回归用例、断言补强建议和 failure wiki；人工确认后才注册到稳定资产。 | 失败 run、assertions、logs、人工确认。 | failure case package、regression task、registry、failure-pattern wiki。 | `scripts/generate_failure_case.py`、`scripts/register_failure_case.py`、`docs/wiki/voice-validation/failure-patterns/` |
| 报告与可追溯层 | 生成用户可读总报告，串起唤醒、识别拼音/中文、在线 `mid/sessionId/recordId`、TTS/media/control 结果和证据目录。 | run 目录、interaction trace、assertions、stress summary。 | report md/json/csv、请求 ID 索引、覆盖矩阵。 | `scripts/build_validation_summary_report.py`、`scripts/build_command_control_summary_report.py`、`scripts/extract_online_request_ids.py` |
| 稳定性压测层 | 长时间随机交互，统计轮次、成功率、异常轮、误唤醒/误识别、媒体错误、重启/crash/watchdog。 | 压测 task、语料池、随机权重、运行时长/轮次。 | rounds.csv、summary_final.json、异常窗口、趋势分析。 | `scripts/run_online_mixed_stress.py`、`scripts/analyze_online_stress.py`、唤醒压测脚本 |
| 声学/媒体 Oracle 层 | 校验“设备日志说播了”和“真实声学是否可证明”之间的差异；无回采设备时只给日志级结论或 BLOCKED。 | 播放声卡、capture/loopback 声卡、媒体日志、ffmpeg/laid。 | acoustic metrics、media oracle report、RMS/峰值/有效时长/削波、oracle gap。 | `tools/audio/polaris_acoustic_oracle.py`、`scripts/analyze_media_response_oracle.py`、`tools/audio/polaris_laid.py` |

这套分层的核心约束是：

- 需求不直接驱动临时脚本，必须先落到知识库、BDD/Task/Scene 或 registry。
- 执行不由大模型临场决定，必须走 Adapter、Runner、Kernel 这些确定性入口。
- 判断不靠人工看几行日志猜测，必须走 Event Runtime、状态机、断言和 coverage。
- 失败不只给口头结论，必须留下证据目录、归因链路，并能反哺 failure wiki 或回归用例。

### 0.5 执行结果会记录哪些关键信息

后续报告和单用例 `result.json` 会尽量记录“有助于复盘”的完整链路：

| 信息 | 说明 |
|---|---|
| 唤醒 | 唤醒时间、唤醒词、唤醒拼音、来源串口和日志行 |
| 识别 | 期望语料、实际识别中文、识别拼音、本地 keyword/拼音、额外误识别 |
| 在线请求 | `mid`、`sessionId`、`recordId`、`topic`、`deviceId`、`sn`、`clientId` |
| 云端响应 | `cloud.speech.trans.ack`、`cloud.instructions.audioBroadcast`、`cloud.speech.reply`、`mideaSkillId`、TTS/media URL |
| 设备响应 | TTS/media 播放、控制回复、蜂鸣器/执行反馈证据、媒体错误 |
| 稳定性 | reboot、crash、watchdog、panic、串口断流、HTTP/player/media error |
| 归因 | 环境、声卡、串口、设备、固件、云端、需求、时序、oracle 缺口 |

核心目标是做到：

```text
一次唤醒 -> 一次识别 -> 一次云端请求 -> 一次设备响应 -> 一个结论
```

### 0.6 已验证/沉淀的典型能力

- 唤醒：首次唤醒、识别模式唤醒、连续唤醒、随机间隔唤醒、唤醒耗时、误唤醒 smoke。
- 命令词：FA2 命令词、基础控制命令、查询命令、同义词/拼音 oracle、控制链路分段断言。
- 半/全双工：模式切换、云控下发、识别窗口、超时、连续识别、播报中监听/打断。
- 在线交互：新闻、音乐、相声、百科、炒菜问答、在线命令、媒体/TTS/MP3 响应。
- 打断：自播窗口测量、唤醒打断、命令打断、临界时序保护。
- 稳定性：夜间在线随机混合压测、唤醒压测、异常轮次统计、重启/崩溃归因。
- 项目拓扑：`cskwb01` 四串口 AP+CP+ASR+control；`venusws63` 三串口 AP+upper+control，无 CP 自动降级。

### 0.7 亮点

- **不依赖临时脚本**：BDD 用例只要映射到 registry，后续执行不需要大模型动态改脚本。
- **真机证据优先**：串口、声卡、云控、媒体、重启、执行产物都落到 debug 证据目录。
- **断言可归因**：区分固件问题、设备/环境问题、云端问题、需求问题、时序不确定、oracle 缺口。
- **链路级报告**：在线场景会保留 `mid/sessionId/recordId`，方便云端按请求 ID 查音频。
- **项目可复用**：通过 `polaris.local.json` 切换 WB01/WS63/新项目，不把端口和环境写死在脚本里。
- **持续学习**：新资料进入 `docs/intake/`，通用方法进 `docs/wiki/`，项目差异进 `docs/knowledge/<project_id>/`。
- **稳定性压测闭环**：长时间压测不仅统计 PASS/FAIL，还记录误识别、媒体错误、重启、crash、watchdog 等异常窗口。

### 0.8 新人最小上手路径

```text
1. 复制 polaris.local.example.json -> polaris.local.json
2. 配置 active_project、串口、声卡、UAT/SIT、Wi-Fi
3. 执行 python tools\audio\polaris_laid.py ensure/list 检查声卡
4. 用 run_optimized_task.py 跑 first_wake smoke
5. 用 generate_requirement_package.py 针对需求生成方案和用例
6. 确认后 execute 真机执行
7. 看 debug/<run>/execution_record.json、result.json、report.md、summary.json
```

## 1. 适合解决什么问题

- 语音唤醒：首次唤醒、识别模式下唤醒、连续唤醒、唤醒率压测。
- 语音识别：基础命令词、需求命令词、自由说小样本、one-shot 间隔矩阵。
- 交互模式：半双工识别、全双工识别、识别超时、临界超时保护。
- 打断验证：设备自播/TTS/媒体播放过程中进行唤醒打断或命令打断。
- 在线交互压测：基础命令、音乐、相声、新闻、问答、组合场景随机压测。
- 异常归因：重启、crash、看门狗、误唤醒、误识别、额外识别结果记录。
- 项目化复用：同一套框架支持 `cskwb01`、`venusws63` 等不同串口拓扑。

## 2. 当前目录结构

```text
Polaris/
  README.md                         # 当前文档，新人入口
  SKILL.md                          # Codex skill 说明，描述必须遵守的工作流
  AGENTS.md                         # 本仓库 agent 启动规则：每次先读/写 plan.md
  polaris.local.example.json         # 本机配置模板，提交到 git
  polaris.local.json                 # 本机真实配置，不提交 git
  plan.md                            # 本地执行计划和进度，不提交 git
  .gitignore                         # 忽略本地配置、运行产物、旧归档
  docs/                              # 文档、需求、命令词、表格、学习入口
    fa2命令词.txt                    # 默认命令词文件
    requirements/                    # 需求文档、词表、自由说资料
    cases/                           # 用例表格资料
    api/                             # 云控/API 相关辅助代码
    intake/                          # 新项目/新功能资料导入入口
    wiki/                            # 长期 Wiki：测试方法、断言归因、验证包
    knowledge/                       # 学习后的结构化知识沉淀
    skill/                           # 当前 skill 设计、能力、落地说明
  satellite/cucumber-agent-testing/  # Cucumber/BDD + Event Runtime 主体
    features/                        # Cucumber feature 用例
    references/                      # step/action/assertion mapping、策略池、能力沉淀
    tasks/                           # 可直接运行的任务 JSON
    configs/                         # 旧兼容示例配置；根目录配置优先
    scripts/                         # run_task/run_cucumber/replay/压测入口脚本
    runtime/                         # Event Runtime 内核、插件、解析器、断言、replay
    debug/                           # 运行产物目录，不提交 git
  tools/                             # 最小工具层：串口、声卡、云控、case runner 支撑
  oldTime/                           # 旧方案完整归档，不作为当前执行入口
```

> 当前只保留一个 `docs/` 目录；旧 `doc/`、`config/`、`result/`、`cache/`、`outputs/`、`_runtime/`、`spec/`、`references/` 等混杂入口不再作为新方案入口。

## 3. 框架是怎么工作的

```text
Cucumber Feature / Task JSON
        ↓
run_task.py 读取任务和 polaris.local.json
        ↓
compile_feature.py 可选离线编译 step/action/assertion plan
        ↓
run_cucumber.py 调用固定动作：串口、声卡、云控、日志采集
        ↓
运行产物写入 satellite/cucumber-agent-testing/debug/
        ↓
runtime_replay.py / Event Runtime 解析产物为 ValidationEvent
        ↓
Timeline + StateMachine + Assertion Engine
        ↓
输出 replay_package.json、assertions.json、runtime_replay_report.md
```

关键原则：

- Cucumber 只表达“要验证什么”，不把复杂判断写进自然语言。
- runner 只执行已注册的动作，不临时让大模型生成脚本。
- Runtime 只根据事件和固定断言判断结果，不让大模型决定 PASS/FAIL。
- 所有 wake/asr/media/network/reboot 等能力逐步走 plugin 化，避免 runtime 变成巨型脚本。
- 断言用 monotonic timeline 做时序判断，wall clock 只用于报告展示。

## 4. 首次使用步骤

### 4.1 克隆后准备本机配置

```powershell
Copy-Item polaris.local.example.json polaris.local.json
notepad polaris.local.json
```

只需要优先改这些字段：

| 配置 | 说明 |
|---|---|
| `active_project` | 当前连接的项目，例如 `cskwb01` 或 `venusws63`。 |
| `common.audio.default_playback_device_key` | 指定声卡 key；留空则使用电脑默认声卡。 |
| `common.audio.capture_device_key` / `loopback_device_key` | 可选；有音频回采时填写，用于证明“真的出声”和播放质量。 |
| `common.device.wake_word` | 当前唤醒词，例如 `小美小美`。 |
| `common.network.wifi_ssid/password` | 当前测试 Wi-Fi 或热点信息。 |
| `projects.<项目>.serial.ports` | AP/CP/ASR/上位/控制口 COM 口。 |
| `projects.<项目>.cloud.api_environment` | 云控环境，常见为 `uat` 或 `sit`。 |
| `projects.<项目>.cloud.device_env_command` | 设备切换环境命令，必须发到设备支持的串口。 |
| `projects.<项目>.cloud.capabilities` | 可选；声明云控权限，例如 `volume_control`、`night_mode`、`wake_word_config`、`wake_threshold`、`multi_wake`。 |

### 4.1.1 首次运行先检查声卡查询命令

多声卡机器需要用稳定声卡 key 填 `common.audio.default_playback_device_key`。Polaris 已自带 `laid` 检查/安装脚本，目录固定在 `tools/audio/laid/`，不要把安装脚本散放到根目录。

```powershell
# 检查当前电脑是否已经有 laid
python tools\audio\polaris_laid.py check

# 没有就安装/刷新到当前用户 PowerShell profile；已存在则不重复安装
python tools\audio\polaris_laid.py ensure

# 查询播放声卡 key，复制 Render 行的 DeviceKey 到 polaris.local.json
python tools\audio\polaris_laid.py list --direction Render
```

也可以直接在 PowerShell 中执行 `laid`。如果 `laid` 不存在，执行 `ensure` 后重新打开 PowerShell，或按安装输出提示重新加载 profile。Linux 环境使用同一个 Python 入口，会调用 `tools/audio/laid/install_laid_linux.sh` 写入 `~/.bashrc`/`~/.zshrc`。

TTS 合成凭据不写死在仓库中；如需使用讯飞 TTS，设置 `POLARIS_XFYUN_APP_ID`、`POLARIS_XFYUN_API_KEY`、`POLARIS_XFYUN_AUTH_ID`（或兼容的 `XFYUN_*` 环境变量）。未配置时，Windows 会回退本机 SAPI 合成。

### 4.2 WB01 项目最小配置

`cskwb01` 通常是 AP + CP + ASR/WB01 + 控制口四串口：

```json
{
  "active_project": "cskwb01",
  "projects": {
    "cskwb01": {
      "serial": {
        "ports": {
          "ap": "COM13",
          "cp": "COM14",
          "asr": "COM11",
          "control": "COM12"
        }
      },
      "cloud": {
        "api_environment": "sit",
        "device_env": "sit"
      }
    }
  }
}
```

注意：如果声卡播放成功但设备无唤醒证据，优先在 `control` 串口执行 PA 前置：

```text
uut-pa.on
pa-enable.set 0 17 0 1
```

这两个命令必须发到控制口，不要发到 AP/CP/ASR 口。

### 4.3 WS63 项目最小配置

`venusws63` 通常是 AP + 上位/WiFi + 控制口三串口，没有独立 CP：

```json
{
  "active_project": "venusws63",
  "projects": {
    "venusws63": {
      "serial": {
        "ports": {
          "ap": "COM20",
          "upper": "COM17",
          "asr": "COM17",
          "cp": "",
          "control": "COM19"
        }
      },
      "cloud": {
        "api_environment": "uat",
        "device_env": "uat"
      }
    }
  }
}
```

WS63 没有 CP 时，`cp` 必须留空；断言会根据 capability 自动降级，不会强行要求 CP 日志。

## 5. 常用执行方式

### 5.0 从需求生成方案/用例确认包

用户只给一句需求时，先用已沉淀的 Wiki/验证包生成可复核的方案、用例矩阵、缺口和执行确认单；这个步骤不碰真机，也不依赖大模型在线生成脚本：

```powershell
python satellite\cucumber-agent-testing\scripts\generate_requirement_package.py --requirement "在线全双工相关功能验证"
```

默认输出到 `satellite/cucumber-agent-testing/debug/requirement_packages/<时间戳>/`，包含 `test_plan.md`、`case_matrix.md`、`gap_list.md`、`confirmation.md` 和 `requirement_package.json`。

### 5.1 只打印将要执行的命令

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json --print-command
```

### 5.2 dry-run 检查流程，不碰真机

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json --mode dry-run
```

### 5.3 真机执行

真机执行会占用串口、声卡、热点或云控，所以必须显式允许副作用：

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json --mode execute --allow-side-effects --manage-session
```

### 5.4 先编译 Cucumber 再执行

用于验证 feature 是否能通过 registry 固化执行，不依赖临时脚本生成：

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json --compile-first --mode execute --allow-side-effects --manage-session
```

### 5.5 在线混合压测

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_mixed_stress.example.json --mode execute --allow-side-effects
```

WS63 示例：

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_mixed_stress.ws63.example.json --mode execute --allow-side-effects
```

### 5.6 在线全双工验证

在线全双工建议走优化任务入口，让声卡检查、设备 UAT/SIT 环境切换、联网确认、全双工 API 下发、Cucumber 主流程和 Runtime replay 都有独立证据：

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_full_duplex.example.json --mode execute --allow-side-effects --manage-session --runtime-strict
```

先不碰真机时：

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_full_duplex.example.json --mode dry-run
```

详细验证包见 `docs/wiki/voice-validation/packs/online-full-duplex.md`；运行期断言细节见 `docs/knowledge/common/online_full_duplex_validation.md`。

注意：云控 adapter 会把当前任务的 `--env-file` 继续传给 `tools/cloud/polaris_app_control.py`。因此 WB01/WS63 或新项目切换时，API 辅助脚本应读取当前项目的 AP/ASR/upper 串口和 UAT/SIT 环境，不能回退到旧 `config/` 或根目录 active_project。

### 5.7 对已有日志做 replay 和断言

```powershell
python satellite\cucumber-agent-testing\scripts\runtime_replay.py --input-dir satellite\cucumber-agent-testing\debug\runs\<某次运行目录> --profile first_wake --env-file polaris.local.json
```

replay 输出默认在：

```text
satellite/cucumber-agent-testing/debug/runtime_replay/<时间戳>_<profile>/
```

重点看：

```text
assertions.json              # 机器可读断言结果
runtime_replay_report.md     # 人可读报告
events.json                  # 标准化事件
timeline.json                # monotonic 时间线
runtime_state.json           # 状态机结果
replay_package.json          # 完整 replay 包
```

`runtime_state.json` 里重点看 `state_health`、`state_violations` 和 `coverage`：它们分别表示状态机健康度、Crash/Reboot/音频媒体顺序/识别前因等 guard 违规、以及状态迁移覆盖情况。

如果要对 replay timeline 写轻量业务断言，可使用 `run_assertion_dsl.py`。当前支持：

```text
EXPECT WakeDetected
EXPECT WakeDetected WITHIN 3000ms AFTER AudioInjected
FORBID RebootDetected FOR 10000ms AFTER WakeDetected
EXPECT_SEQUENCE WakeDetected -> ASRDetected -> MediaStarted WITHIN 15000ms
EXPECT_RESPONSE TTSStarted|MediaStarted WITHIN 15000ms AFTER ASRDetected|CommandDetected
EXPECT_DURATION MediaStarted TO MediaCompleted >= 500ms
```

### 5.7 优化执行封装：执行记录、重试和资源预检

`run_optimized_task.py` 是当前按两份 Runtime 优化方案新增的第一层工程化入口。它不会替换 `run_task.py`，而是在外层增加：

- 资源/约束 preflight：串口、声卡、网络、云环境、副作用策略、项目拓扑。
- execute 模式串口打开探测：如果端口被 Xshell/串口助手/旧 logger 占用，先判 `BLOCKED`，避免误判成固件 FAIL。
- Adapter Flow 前置/收尾：可在主 Cucumber runner 前后执行 PA、环境切换、联网、音量等固定 flow。
- 执行前后状态快照：`state/before.json`、`state/after.json`。
- 状态差异：`state_diff.json`。
- 尝试记录：`attempts.jsonl` 和每次 attempt 的 `stdout.log`。
- 汇总记录：`execution_record.json`，包含 `PASS`、`STABLE_FAIL`、`FLAKY_PASS`、`ENV_RELATED` 等分类。

只做预检，不碰真机：

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json --mode dry-run --precheck-only
```

打印将要执行的底层命令：

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json --mode dry-run --print-command
```

执行完成后，把一次或多次 `optimized_runs` 汇总成用户可读总报告：

```powershell
python satellite\cucumber-agent-testing\scripts\build_validation_summary_report.py --run satellite\cucumber-agent-testing\debug\optimized_runs\<某次optimized_run>
```

默认输出到 `satellite/cucumber-agent-testing/debug/reports/<时间戳>/validation_summary_report.md`。

真机执行并允许一次失败重试：

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json --mode execute --allow-side-effects --manage-session --runtime-strict --max-retries 1
```

默认输出目录：

```text
satellite/cucumber-agent-testing/debug/optimized_runs/<时间戳>_<task_id>/
```

重点看：

```text
preflight.json
command.json
execution_record.json
adapter_flows/pre.json
adapter_flows/post.json
attempts.jsonl
state/before.json
state/after.json
state_diff.json
attempt_01/stdout.log
```

### 5.8 Kernel 生命周期与 Scene 调度

`run_validation_kernel.py` 是当前推荐的统一入口。它先把 `task + env` 编译为 Validation IR，再输出 adapter/capability/resource/constraint 快照；如果带 `--execute-runner`，会继续调用 `run_optimized_task.py`，并在真机执行后自动生成 replay 侧证据。

单任务 dry-run：

```powershell
python satellite\cucumber-agent-testing\scripts\run_validation_kernel.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json --env-file polaris.local.json --mode dry-run --execute-runner
```

scene 通过 Kernel 执行：

```powershell
python satellite\cucumber-agent-testing\scripts\run_kernel_scene.py --scene satellite\cucumber-agent-testing\debug\validation\scene_smoke.json --env-file polaris.local.json --mode dry-run --execute-runner
```

如果只想检查 scene/task 是否能统一编译成 IR，可以输出 scene 级 bundle：

```powershell
python satellite\cucumber-agent-testing\scripts\run_kernel_scene.py --scene satellite\cucumber-agent-testing\debug\validation\scene_smoke.json --env-file polaris.local.json --mode dry-run --emit-ir-bundle --print-command
```

真机执行时仍必须显式允许副作用：

```powershell
python satellite\cucumber-agent-testing\scripts\run_validation_kernel.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json --env-file polaris.local.json --mode execute --execute-runner --allow-side-effects --manage-session --runtime-strict
```

Kernel 重点产物：

```text
kernel_record.json
lifecycle.jsonl
validation_ir.json
scene_validation_ir_bundle.json  # scene 执行且带 --emit-ir-bundle 时生成
adapter_registry.json
capability_matrix.json
resource_snapshot.json
constraint_result.json
runtime_analysis.json        # 真机 replay 存在时生成
post_analysis/*/event_graph.json
post_analysis/*/state_assertions.json
post_analysis/*/replay_vm_state.json
```

`runtime_analysis.json` 会同时汇总 profile 断言、默认状态断言、状态覆盖策略和 `state_health`。如果出现 Crash 后继续业务、Reboot 后无恢复标记就继续识别、音频/媒体证据顺序不完整等状态机 guard 违规，或 profile 要求的 Wake/ASR/Media/Network 状态覆盖缺失，会在这里单独呈现，便于区分稳定性问题、日志缺口和业务断言失败。状态覆盖策略支持 `coverage.projects.<project_id>` 覆盖项，可按 WB01、WS63 或新项目单独收紧/放宽阈值。

## 6. Task JSON 怎么写

推荐先复制 `satellite/cucumber-agent-testing/tasks/examples/` 下的模板。

最小结构如下：

```json
{
  "schema": "polaris.task.v1",
  "runner": {
    "mode": "dry-run",
    "compile_first": true,
    "feature": "satellite/cucumber-agent-testing/features/polaris_voice_core.feature",
    "mapping": "satellite/cucumber-agent-testing/references/voice_core_mapping.json"
  },
  "scenario": {
    "tag": "first_wake"
  },
  "environment": {
    "env_file": "polaris.local.json"
  },
  "inputs": {
    "command_file": "docs/fa2命令词.txt"
  },
  "execution": {
    "observe_ms": 15000,
    "manage_session": true,
    "allow_side_effects": false,
    "adapter_flows": {
      "pre": [
        {"flow": "pa_recover", "when": "dry-run", "required": false}
      ],
      "post": [
        {"flow": "hotspot_status", "when": "dry-run", "required": false}
      ]
    }
  }
}
```

字段含义：

| 字段 | 用途 |
|---|---|
| `runner.mode` | `plan-only`、`dry-run`、`execute`。 |
| `runner.compile_first` | 是否先通过 registry 编译成确定性执行计划。 |
| `scenario.tag` | 要执行的 Cucumber 场景 tag，例如 `first_wake`。 |
| `environment.env_file` | 默认使用根目录 `polaris.local.json`。 |
| `inputs.command_file` | 命令词文件，默认 `docs/fa2命令词.txt`。 |
| `execution.allow_side_effects` | 真机执行必须为 `true` 或命令行传 `--allow-side-effects`。 |
| `execution.manage_session` | 是否自动建立/关闭串口日志会话。 |
| `execution.adapter_flows.pre/post` | 在 `run_optimized_task.py` 主流程前/后执行固定 Adapter Flow；默认 dry-run，execute 模式且允许副作用时才会真实执行。 |

`adapter_flows` 适合放稳定前置动作，例如 PA 恢复、AP 环境切换、联网确认、音量/半全双工云控。每个 flow 支持 `when`、`required`、`execute` 和 `params`；`required=true` 的 pre flow 失败会阻断主流程，避免前置没完成还误判固件 FAIL。

## 7. 当前已注册测试项

这些 tag 可以通过 task 或 `run_cucumber.py --tag <tag>` 触发：

```text
first_wake
recognition_mode_wake
half_duplex_recognition
full_duplex_recognition
basic_command_recognition
requirement_command_smoke
requirement_free_speech_smoke
interrupt_prerequisite_measurement
wake_interrupt
command_interrupt
network_recovery_basic
offline_oneshot_matrix
online_oneshot_matrix
false_wake_quiet_basic
wake_latency_smoke
continuous_wake_smoke
random_interval_wake_smoke
online_vad_special_smoke
attribution_validator_smoke
false_wake_human_speech_smoke
false_wake_white_noise_smoke
```

## 8. 断言和归因口径

Runtime 会先把证据转换为标准事件，例如：

```text
AudioInjected
WakeDetected
ASRDetected
CommandDetected
TTSStarted
MediaStarted
MediaCompleted
NetworkLost
NetworkRecovered
RebootDetected
CrashDetected
```

然后按 profile 做断言：

| 结果 | 含义 |
|---|---|
| `PASS` | 证据满足功能意图，时序和禁止行为也满足。 |
| `FAIL` | 有足够证据证明设备行为不符合预期。 |
| `BLOCKED` | 环境、串口、声卡、云控、日志缺失等导致无法判断。 |
| `PASS_WITH_SKIPPED_TIMING` | 主功能通过，但部分时序证据不足，只能跳过时序断言。 |
| `TIMING_AMBIGUOUS` | 临界超时或播报占用导致时序不可稳定归因，需要复核。 |

首唤醒时序的锚点策略已经固化到 `runtime/assertion_engine.py`：

- 优先使用真实 `AudioInjected` 到首个物理唤醒簇的时差。
- 如果主机播放进程明显长于 wav 时长，说明 `AudioInjected` 更像“播放进程启动”而不是“声波到达设备”；此时优先用 `AudioCompleted - audio_duration_ms` 估算有效波形起点。
- 如果仍无法稳定估算，但唤醒落在播放窗口内，结果标记为 `TIMING_AMBIGUOUS`，不直接归因为固件超时。
- WB01 要求 CP/AP/ASR 唤醒闭环；WS63 没有 CP 时按 AP/ASR 闭环断言。

归因原则：

- 没有发音频却出现 wake/ASR/command，要记录为疑似误唤醒或误识别。
- 有声卡播放但无任何设备侧证据，优先判为环境/设备链路阻塞，而不是直接判固件失败。
- 有 AP/ASR 识别但无预期响应，需要结合媒体/TTS/云控事件判断是识别问题、执行问题还是云端问题。
- 出现 `RebootDetected`、`CrashDetected`、watchdog、panic、hardfault 等标记，要单独进入健康度和稳定性统计。

## 9. Runtime Phase 2 最小落地：执行记录、资源和约束

当前已经新增 Phase 2 的最小闭环，不做复杂平台化，只先保证本地真机任务更可控：

```text
runtime/
  resource_runtime.py       # ResourceClaim/ResourceSnapshot，检查串口/声卡/网络/云控/电源资源冲突
  constraint_engine.py      # task/env preflight，检查副作用、串口拓扑、在线网络、云环境、打断 guard
scripts/
  run_optimized_task.py     # 包装 run_task.py，产出 execution_record、attempts、state snapshot
references/optimization/
  execution_record.schema.json
  retry_policy.json
```

这一步的目标不是替代现有 Cucumber runner，而是给每次执行补上“可审计上下文”。后续做场景引擎、失败聚类、设备健康度时，都以 `execution_record.json` 为输入。

继续对齐两份优化方案后，当前还提供这些轻量入口：

```text
scripts/generate_scene.py           # strategy -> scene graph
scripts/run_scene.py                # scene graph -> run_optimized_task
scripts/analyze_execution_store.py  # execution_record -> failure fingerprint / health report
scripts/replay_vm.py                # replay package -> VM-lite snapshot/time travel
scripts/simulate_runtime.py         # Fake log -> replay smoke
scripts/run_assertion_dsl.py        # EXPECT/FORBID/SEQUENCE/RESPONSE/DURATION DSL-lite
scripts/inspect_device_adapters.py  # env -> adapter registry
scripts/build_capability_matrix.py  # env -> project capability matrix
scripts/build_event_graph.py        # timeline/run_dir -> causal event graph + risk summary
scripts/run_state_assertion_dsl.py  # runtime_state -> state assertions
scripts/run_state_coverage_policy.py # runtime_state -> profile coverage PASS/WARN/FAIL
scripts/compile_validation_ir.py    # task/scene/compiled feature plan + env -> Validation IR / bundle
scripts/run_validation_kernel.py    # task + env -> Kernel lifecycle record
scripts/run_kernel_scene.py         # scene graph -> per-node Kernel lifecycle record
scripts/run_adapter_action.py       # adapter registry action -> command plan/execute，默认 dry-run
scripts/plan_adapter_flow.py        # high-level setup/execution flow -> adapter action sequence
scripts/build_analytics_trend.py    # execution_record history -> local trend report
docs/skill/runtime-implementation-matrix.md
tools/audio/polaris_laid.py         # 检查/安装 laid，并查询 ListenAI 声卡稳定 key
```

常见前置/调试流程可以先通过 Adapter Flow dry-run，确认串口、声卡、云控和网络动作会走哪个底层工具：

```powershell
python satellite\cucumber-agent-testing\scripts\plan_adapter_flow.py --flow pa_recover --env-file polaris.local.json
python satellite\cucumber-agent-testing\scripts\plan_adapter_flow.py --flow wake_audio_file --env-file polaris.local.json --param audio_file=sample.wav
```

当前真机验证摘要：

- WB01：当前本地串口映射为 AP `COM13@921600`、CP `COM14@921600`、WB01/ASR `COM11@921600`、control `COM12@115200`；WB01 云控异常不阻塞 WS63 本轮上线试运行。
- WS63：当前本地串口映射为 AP `COM20@921600`、upper/asr `COM17@921600`、control `COM19@115200`，声卡 `VID_8765&PID_5678:9_27F546DA_3_0000`；`check-env`、云控恢复和最终快照均已真机验证。
- WS63 专项结论：command-control 扩大矩阵 12/12 FAIL，root_cause=`command_or_audio_baseline`；online VAD 12/12 FAIL，归因 `wake_precondition_for_online_vad`；offline one-shot 修复 runtime 假阴性后 PASS。
- `run_optimized_task.py` 已修正聚合逻辑，会按 scenario/runtime 结果输出 `FAIL`、`BLOCKED`、`TIMING_AMBIGUOUS` 或 `PASS`，不会把 `status=DONE` 误当 PASS。
- Adapter/Capability/EventGraph/StateDSL/ValidationIR/AnalyticsTrend 已完成本地 MVP，WB01 与 WS63 都做了 smoke 验证；Validation IR 现在支持 task、scene node、scene bundle、compiled feature plan bundle，详细数字见 `docs/skill/runtime-implementation-matrix.md`。
- Validation Kernel 生命周期已完成本地 MVP：`run_validation_kernel.py` 会产出 `kernel_record.json`、`lifecycle.jsonl`、`validation_ir.json`、adapter/capability/resource/constraint 快照，并可委托 `run_optimized_task.py` 执行。
- Kernel scene 调度已接入：`run_kernel_scene.py` 会让 scene 每个节点都走 Kernel 生命周期；真机执行后的 runtime replay 会自动补齐 event graph、默认 state assertions、state coverage policy、Replay VM-lite snapshot 和 `runtime_analysis.json`。

Event Graph 支持项目级规则 overlay：把私有云端/媒体/TTS marker 规则写到 `satellite/cucumber-agent-testing/references/optimization/event_graph_rules.json`，或执行时通过 `build_event_graph.py --rules <file>` 指定。规则可把“ASR/Command 后出现某类 TTS/Media marker”补成项目专属因果边，不需要改核心 `runtime/event_graph.py`。

## 10. Event Runtime Phase 1 结构

当前已开始按高级优化方案做 Phase 1：

```text
runtime/
  events.py                # ValidationEvent v1 schema，含 plugin、severity、tags、wall/monotonic 时间
  timeline.py              # monotonic timeline，断言使用相对单调时间
  state_machine.py         # 运行状态机，输出并行状态、迁移、guard 违规和覆盖率
  assertion_engine.py      # 固化断言逻辑
  replay.py                # replay package 构建
  kernel/
    plugin.py              # RuntimePlugin、PluginManager、PluginContext
  plugins/
    wake.py                # 唤醒域插件
    asr.py                 # ASR/命令识别域插件
    media.py               # TTS/媒体/打断域插件
    network.py             # 网络域插件
    reboot.py              # 重启/crash 域插件
  parsers/
    serial_log_parser.py   # 串口/播放日志解析
    json_artifact_parser.py# 结构化产物解析
```

后续新功能不要直接塞进一个巨大的 runtime 文件里，优先按领域进入 plugin：wake、asr、media、network、reboot，必要时再新增 plugin。

## 11. Wiki 知识库怎么用

`docs/wiki/` 是当前新逻辑的长期知识库，用来承接旧 `voice-test-plan-designer` 和后续新项目/新功能资料。后续我生成测试方案和用例时，应优先查这里，而不是每次从零调试。

常用入口：

- `docs/wiki/voice-validation/test-item-index.md`：语音测试项总表、当前能力等级、已有 tag/task 和缺口。
- `docs/wiki/voice-validation/wakeup.md`：首次唤醒、识别模式唤醒、打断唤醒、响应时间等方法。
- `docs/wiki/voice-validation/command.md`：命令词识别、反集/集外、one-shot、打断、自激等方法。
- `docs/wiki/voice-validation/online-recognition.md`：半双工、全双工、在线 VAD、弱网和云端稳定性。
- `docs/wiki/voice-validation/assertion-attribution.md`：PASS/FAIL/BLOCKED/TIMING_AMBIGUOUS/需求复核归因规则。
- `docs/wiki/voice-validation/packs/`：可复用功能验证包，当前包含首次唤醒、识别模式唤醒、半双工、在线全双工、基础命令词、在线混合压测和误唤醒。
- `docs/wiki/voice-validation/new-project-feature-intake.md`：新项目/新功能资料如何从 intake 沉淀成 Wiki、knowledge 和可执行 runtime。
- `docs/wiki/voice-validation/failure-feedback.md`：压测/真机异常如何反哺新用例、断言、Event Graph rule 和 coverage 阈值。
- `docs/wiki/voice-validation/project-rule-overlays.md`：WB01/WS63/新项目私有 marker、Event Graph overlay 和 coverage 阈值沉淀规则。

在线全双工完整矩阵不仅有 smoke，还提供 FD-002~FD-012 的 task/scene：

```powershell
python satellite\cucumber-agent-testing\scripts\run_kernel_scene.py --scene satellite\cucumber-agent-testing\references\scenes\online_full_duplex_fd002_fd012.scene.example.json --mode dry-run --execute-runner --emit-ir-bundle
```

新项目/新功能的沉淀规则：原始资料放 `docs/intake/`，学习后的长期方法补到 `docs/wiki/`，项目差异补到 `docs/knowledge/<project_id>/`，能执行的内容再进入 Cucumber/Adapter/Runtime。

## 12. 新项目/新功能怎么导入学习

如果后续有新项目说明、新功能需求、外部测试方案、类似 `voice-test-plan-designer` 的资料，统一放到：

```text
docs/intake/<project_id>/<YYYYMMDD_topic>/
  learning_manifest.json
  raw/
```

操作步骤：

1. 从 `docs/intake/templates/learning_manifest.template.json` 复制 `learning_manifest.json`。
2. 把原始资料放入 `raw/`，例如 PDF、Excel、Markdown、日志、旧 skill。
3. 在 manifest 里写清楚本次目标：项目 profile、功能测试策略、Cucumber 用例、断言逻辑、压测策略或缺口分析。
4. 我学习后会把通用测试方法沉淀到 `docs/wiki/`，把项目差异沉淀到 `docs/knowledge/<project_id>/`。
5. 资料足够时，再把可执行能力写入 `features/`、`references/`、`tasks/`、`runtime/plugins/` 或 `tools/`。

资料不足时，只输出缺口清单，不伪造可执行能力。

## 13. 哪些文件不要提交

这些只属于本机或运行产物：

```text
polaris.local.json
plan.md
oldTime/
satellite/cucumber-agent-testing/debug/
result/
cache/
outputs/
_runtime/
__pycache__/
*.log
*.tmp
```

## 13. 常见问题

### 声卡播放不生效怎么办

- 如果项目/设备配置里没有填写声卡 key，默认使用电脑默认声卡。
- 多声卡环境先执行 `python tools\audio\polaris_laid.py ensure`，再用 `python tools\audio\polaris_laid.py list --direction Render` 查询稳定 key，填入 `common.audio.default_playback_device_key`。
- 如果声卡播放成功但设备无唤醒证据，WB01/WS63 可尝试在控制口执行：`uut-pa.on`、`pa-enable.set 0 17 0 1`。

### 云控/API 设置不生效怎么办

- 先确认 `polaris.local.json` 的 `cloud.api_environment` 是 `uat` 还是 `sit`。
- 再确认设备端 CSK/AP 已切到同一环境。
- 环境切换命令通常在 `projects.<项目>.cloud.device_env_command` 中配置，切换后可能需要重启。
- WS63 还要确认 `version` 输出的 `Project Version`。当前沉淀规则：`35.03.01.01.18.26.05.04.00.02` 已知后台未授权 API 控制，应切到 `35.03.01.01.18.26.05.04.00.01` 后再进入 UAT/SIT。
- 可用 `python tools\cloud\polaris_cloud_diagnostics.py --env-file <你的配置> --probe-cloud` 生成云控诊断报告；HTTP 200 但业务 `code=501` 仍归为 `BLOCKED`，不能判固件 FAIL。
- 详细分析逻辑见 `docs/knowledge/venusws63/cloud-control-version-gate.md`。

### 新写 Cucumber 用例为什么还要 registry

Cucumber 是自然语言入口，但真正执行要靠固定的 step/action/assertion registry。这样做的目的是：

- 用例文本可以变，但执行动作和断言逻辑稳定。
- 执行时不依赖大模型、不依赖网络动态改脚本。
- 同一功能更换唤醒词、超时时间、声卡、串口后仍能复用。

### 老方案还能用吗

旧方案已归档到 `oldTime/`，只作为历史参考，不作为当前执行入口。当前维护和新增能力都应进入新方案目录。

## 当前新增落地能力索引（2026-05-27）

### L1 标准 task/scene

以下能力已经补成标准 task example，可用 `run_optimized_task.py` 或 `run_kernel_scene.py` 触发。默认 `dry-run` 不占用真机；真机执行必须显式增加 `--allow-side-effects --manage-session --runtime-strict`。

- `recognition_mode_wake.example.json`：识别模式下唤醒。
- `half_duplex.example.json`：半双工识别。
- `wake_interrupt.example.json` / `command_interrupt.example.json`：自播中唤醒/命令打断。
- `network_recovery_basic.example.json`：联网恢复基础验证。
- `offline_oneshot_matrix.example.json` / `online_oneshot_matrix.example.json`：one-shot 间隔矩阵。
- `wake_latency.example.json` / `continuous_wake.example.json` / `random_interval_wake.example.json`：唤醒耗时、连续唤醒、随机间隔唤醒。
- `false_wake_quiet.example.json` / `false_wake_human_speech.example.json` / `false_wake_white_noise.example.json`：误唤醒基础 smoke。
- `attribution_validator.example.json`：归因一致性复核。
- 聚合 scene：`satellite/cucumber-agent-testing/references/scenes/l1_voice_core_supported_smoke.scene.example.json`。

### 需求、失败、媒体和报告闭环

- 需求转方案/用例/确认单：

```powershell
python satellite\cucumber-agent-testing\scripts\generate_requirement_package.py --requirement "在线全双工相关功能验证" --project cskwb01 --env-file polaris.local.json
```

- 失败 run 反哺候选用例：

```powershell
python satellite\cucumber-agent-testing\scripts\generate_failure_case.py --run satellite\cucumber-agent-testing\debug\optimized_runs\<run>
```

- 候选经人工确认后注册为稳定回归资产：

```powershell
python satellite\cucumber-agent-testing\scripts\register_failure_case.py `
  --package satellite\cucumber-agent-testing\debug\failure_cases\<pkg>\failure_case_package.json `
  --approve --approved-by <你的名字>
```

- 媒体/TTS/MP3 日志级响应 oracle：

```powershell
python satellite\cucumber-agent-testing\scripts\analyze_media_response_oracle.py --run satellite\cucumber-agent-testing\debug\runs\<run>
```

- 声学回采/loopback oracle（真实出声证据）：

```powershell
python tools\audio\polaris_acoustic_oracle.py probe
python tools\audio\polaris_acoustic_oracle.py self-test
python tools\audio\polaris_acoustic_oracle.py record --env-file polaris.local.json
python tools\audio\polaris_acoustic_oracle.py analyze --audio-file <capture.wav> --env-file polaris.local.json
```

- 多 run 总报告：

```powershell
python satellite\cucumber-agent-testing\scripts\build_validation_summary_report.py --run <run_or_optimized_dir> --run <another_run>
```

媒体 oracle v1 只能证明日志/事件层面的云端 TTS、播放器启动、完成和错误 marker；未配置 `loopback_device_key` 或 `capture_device_key` 时，不声称真实声学出声和音质通过。声学回采 oracle 会分析回采 WAV 的 RMS、峰值、有效时长和削波比例；它只证明“声学信号存在且质量阈值达标”，语义正确仍由串口/ASR/媒体日志断言负责。

## 2026-05-28 真机闭环补充

- 前置 Adapter 现在支持“无 managed session 直接执行”：`serial.ap.set_device_env` 会按当前 `--env-file` 渲染 AP 串口和波特率，直接写串口时使用 `--no-sync-config`，避免把 WS63/WB01 端口串到根本地配置。
- 云控 Adapter 必须传 `--env-file`，并且会校验 HTTP 200 之外的业务码；例如返回 `code=501` 的“设备未上线”会判为 `BLOCKED`，不会误判为设置成功。
- WS63 云控诊断已沉淀到 `tools/cloud/polaris_cloud_diagnostics.py` 与 `docs/knowledge/venusws63/cloud-control-version-gate.md`：先查 `Project Version`，再查 `env=1/2` 是否匹配 UAT/SIT，再查 IoT ID/在线态，最后看云端业务码。
- Cucumber 执行进程会注入 `POLARIS_ENV_FILE`，doc case / network helper 会按当前项目配置读取 UAT/SIT、串口、声卡和设备信息，不再依赖根 `active_project`。
- `run_kernel_scene.py` 支持 `--retry-blocked`，长链路节点遇到声卡/语音链路瞬态 `BLOCKED` 时可配合 `--max-retries` 自动复跑。
- 在线全双工 Runtime 断言已区分 setup/recovery 阶段和主流程：联网恢复阶段重启不直接计入固件失败；Wake->Command 断言要求存在有效事件对，避免 setup 噪声导致误判。
- 媒体/TTS Oracle v1 已过滤 PA 正常关闭超时日志，并继续明确限制：未配置 loopback/capture 时只证明日志级响应，不声称真实声学出声。
- 声学回采 oracle MVP 已提供 `probe/self-test/record/analyze`：没有 `sounddevice/numpy` 或未配置回采设备时输出 `BLOCKED`，不会伪造真实出声 PASS。
- failure-to-test-case 已分成两段：`generate_failure_case.py` 只生成候选；`register_failure_case.py --approve --approved-by <name>` 才会把候选写入 `references/failure_regression_registry.json`、`tasks/generated/regression/`、`references/scenes/generated_failure_regression.scene.example.json` 和 `docs/wiki/voice-validation/failure-patterns/`。
