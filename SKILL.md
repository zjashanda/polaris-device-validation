---
name: polaris-device-validation
version: 2.0.0
summary: Polaris 语音设备 BDD + Event Runtime 真机验证 skill。
---

# Polaris 语音设备验证 Skill

本 skill 已切换到新方案：Cucumber/BDD 用例入口 + Event Runtime 事件断言 + 项目化本机配置。旧方案已迁移到 `oldTime/`，不再作为执行入口。

## 快速定位

Polaris 用来做嵌入式语音设备的本地真机验证，核心是把“用户需求”转成“可执行、可断言、可复盘”的 BDD/Runtime 流程：

```text
需求/任务
  -> 测试方案和 Cucumber 用例
  -> step/action/assertion registry
  -> Adapter Executor 真机动作
  -> 串口/声卡/云控/媒体/网络证据
  -> Event Runtime replay
  -> PASS/FAIL/BLOCKED/WARN/TIMING_AMBIGUOUS + 归因报告
```

当前同时支持两类工作：

- **功能测试验证**：验证唤醒、命令词、半/全双工、在线交互、打断、one-shot、联网恢复等功能是否符合预期。
- **稳定性压测**：长时间随机执行在线音乐/新闻/相声/问答/命令词/组合场景，统计异常、重启、crash、watchdog、无唤醒、无 ASR、媒体错误和误识别候选。

## 整体框架模块口径

向用户或新人解释当前 skill 时，必须按“分层框架”说明，而不是只说几个脚本入口。整体链路是：

```text
项目配置/需求资料
  -> 知识库/Wiki
  -> BDD Feature / Task / Scene
  -> Step-Action-Assertion Registry / Validation IR
  -> Adapter Executor / Validation Kernel
  -> 串口、声卡、云控、网络、上下电等真机证据
  -> Event Runtime: Event Bus + Timeline + StateMachine
  -> Assertion / Coverage / Event Graph
  -> 报告、归因、失败反哺、回归用例
```

各模块职责口径：

- 配置与项目画像层：`polaris.local.json` 记录 active project、串口、声卡、UAT/SIT、Wi-Fi、唤醒词和能力开关，避免脚本写死设备差异。
- 需求理解与知识库层：`docs/intake/` 接收新资料，`docs/wiki/` 沉淀通用方法，`docs/knowledge/<project_id>/` 沉淀项目差异和私有规则。
- BDD/Task/Scene 层：`features/`、`tasks/`、`references/scenes/` 只表达测试意图、用例矩阵和场景组合，不写复杂断言细节。
- Registry/IR 编译层：`voice_core_mapping.json`、`compile_feature.py`、`compile_validation_ir.py` 把自然语言或 task 编译成确定性动作与断言。
- Adapter 执行动作层：串口、声卡、PA/上下电、云控、联网都走固定 adapter；新动作优先补 adapter/registry，不为单用例写临时脚本。
- 会话与资源管理层：负责 managed session、串口覆盖、资源占用和 side-effect 门禁；关键串口打不开要判 `BLOCKED` 或 coverage degraded。
- 执行编排层：`run_task.py`、`run_optimized_task.py`、`run_validation_kernel.py`、`run_kernel_scene.py` 负责 dry-run/execute、重试、前置/收尾和 run 目录。
- 证据采集层：所有 AP/CP/ASR/上位/控制口日志、声卡播放、云控响应、媒体/TTS 产物都必须进入 debug run 目录。
- Event Runtime 层：把日志和产物转换为 `WakeDetected`、`ASRResult`、`CommandMatched`、`TTS/Media/Network/Reboot` 等事件和 timeline。
- 状态机与断言层：用时序、状态、排斥事件、coverage 阈值输出 `PASS/FAIL/BLOCKED/WARN/TIMING_AMBIGUOUS`，并区分固件、设备、环境、需求、时序和 oracle 缺口。
- Event Graph 与归因层：用事件因果边定位 ASR 到 TTS/media/control 的链路断点、重启/crash 风险和项目私有 marker。
- 报告与反哺层：报告必须串起唤醒、识别拼音/中文、在线 `mid/sessionId/recordId`、设备响应和证据路径；失败经确认后进入 failure wiki 和回归用例。
- 稳定性压测层：在线混合压测、唤醒压测等要统计轮次、异常窗口、误唤醒/误识别、媒体错误、重启、crash、watchdog。
- 声学/媒体 Oracle 层：日志级媒体响应和真实声学回采分开判断；没有 capture/loopback 时不能声称“真实出声通过”。

## 工作流要求

用户给测试需求时，优先按下面流程工作，不要退回零散调试模式：

```text
需求解读
  -> 输出测试方案、用例矩阵、正例/反例/异常/边界/稳定性关注点
  -> 等用户确认
  -> 选择已有 task/scene 或补齐 registry/runtime
  -> 真机执行
  -> 输出总报告、失败归因、证据路径、后续沉淀
```

如果是已有能力，例如首次唤醒、基础命令词、在线全双工、在线混合压测，应尽快复用现有 task/scene/registry，不要为单条用例临时写一次性脚本。

## 结果记录要求

每条用例结果尽量记录完整交互链路，方便用户复盘和云端查音频：

- 唤醒：唤醒时间、唤醒词、唤醒拼音、来源串口和行号。
- 识别：期望语料、实际识别中文、识别拼音、本地 keyword/拼音、额外误识别。
- 在线请求：`mid`、`sessionId`、`recordId`、`topic`、`deviceId`、`sn`、`clientId`。
- 云端响应：`cloud.speech.trans.ack`、`cloud.instructions.audioBroadcast`、`cloud.speech.reply`、`mideaSkillId`、TTS/media URL。
- 设备响应：TTS/media 播放、控制回复、蜂鸣器/执行反馈、媒体错误。
- 稳定性：reboot、crash、watchdog、panic、串口断流、HTTP/player/media error。

报告目标是尽量做到：

```text
一次唤醒 -> 一次识别 -> 一次云端请求 -> 一次设备响应 -> 一个结论
```

## 亮点口径

向新人解释本 skill 时，突出这些点：

- BDD 用例不等于临时脚本；执行动作和断言沉淀在 registry/runtime 中，可脱离大模型稳定执行。
- 真机证据优先，所有串口/声卡/云控/媒体/重启/执行产物都要保存到 debug run 目录。
- 断言必须可归因，不能把声卡、串口、云端、UAT/SIT、需求口径、临界时序问题误判成固件 FAIL。
- 在线场景必须保留 `mid/sessionId/recordId`，便于后续到云端按请求 ID 查音频。
- 新项目只改 `polaris.local.json` 和项目知识库，不把 COM 口、声卡、UAT/SIT 写死到脚本。
- 新资料走 `docs/intake/`，通用方法进 `docs/wiki/`，项目差异进 `docs/knowledge/<project_id>/`，持续迭代。

## 必须遵守

1. 每次启动先读取根目录 `plan.md`；没有则创建。
2. 执行计划、已执行、待执行、未执行内容必须及时同步到 `plan.md`。
3. 本机配置只使用根目录 `polaris.local.json`；新人从 `polaris.local.example.json` 复制。
4. 真机执行必须显式 `--allow-side-effects`，避免误占串口、声卡、热点或电源控制。
5. 运行结果、debug、cache、result、`polaris.local.json` 不提交 git。

## Git 同步颗粒度规则

- 同步到 git 时必须以“其他 PC 拉取后能直接使用当前 skill”为标准，不能只提交单个脚本或单个说明文件。
- 一个功能变更需要同时检查并按需提交：`SKILL.md`、README、`orion.skilltest.json`、task 示例、registry/adapter/runtime 代码、配置模板、项目知识库/Wiki、依赖脚本和必要测试数据。
- 新增 zhsh 能力模块时，至少要同步 capability profile、自然语言用例模板、默认 task/runner 入口、前置依赖、副作用/风险口径和路径校验结果。
- 新增或调整真机执行能力时，必须同步配置模板和文档说明；不要只提交本机 `polaris.local.json`，应更新 `polaris.local.example.json` 或对应 `.example.json`。
- 提交前必须做最小可用性校验：JSON 能解析、引用路径存在、关键 Python 文件能 `py_compile`，必要时执行 dry-run；校验结果同步到 `plan.md`，但 `plan.md` 本身不提交。
- 禁止把运行产物、debug、cache、临时文件、真实本机配置、token 或设备私密账号提交到 git。
- 如果发现当前工作目录不是 git 仓库，必须先定位真正的 skill 发布仓库；不要在项目运行目录临时 `git init` 后提交，避免其他 PC 拉错仓库。

## 文件编码与乱码校验规则

- 生成或修改 `SKILL.md`、README、Wiki、Markdown、JSON、YAML、配置模板和 zhsh profile 时，统一使用 UTF-8 保存；中文内容不能出现连续问号、Unicode 替换字符、常见中文乱码字样或典型 mojibake。
- 提交前必须至少用 Python 以 `encoding="utf-8"` 读取目标文件，确认能正常解析/读取；JSON 文件还必须 `json.load` 成功。
- 对中文可展示文件必须做乱码特征扫描：检查是否包含 Unicode replacement character、异常密集问号、常见中文乱码字样，以及 UTF-8 被错误按 ANSI/GBK 展示的特征片段。
- 如果终端显示乱码但 Python UTF-8 读取正常，要以字节和 UTF-8 解码校验为准；如果文件内容本身已经损坏，必须先恢复为正常中文再提交。
- 从脚本批量生成中文文件时，脚本源码、输出文件和校验脚本都要明确 UTF-8；不要依赖 PowerShell 当前代码页隐式编码。
- git 同步前的完整性检查必须覆盖编码校验结果，避免其他 PC 拉取后 `SKILL.md`、`orion.skilltest.json` 或文档无法正常阅读。

## 主要入口

- 任务入口：`satellite/cucumber-agent-testing/scripts/run_task.py`
- 优化任务入口：`satellite/cucumber-agent-testing/scripts/run_optimized_task.py`
- Cucumber 入口：`satellite/cucumber-agent-testing/scripts/run_cucumber.py`
- Runtime replay：`satellite/cucumber-agent-testing/scripts/runtime_replay.py`
- 需求包生成：`satellite/cucumber-agent-testing/scripts/generate_requirement_package.py`
- 总报告汇总：`satellite/cucumber-agent-testing/scripts/build_validation_summary_report.py`
- 在线混合压测：`satellite/cucumber-agent-testing/scripts/run_online_mixed_stress.py`
- 压测分析：`satellite/cucumber-agent-testing/scripts/analyze_online_stress.py`
- 新资料学习入口：`docs/intake/<project_id>/<YYYYMMDD_topic>/learning_manifest.json`
- 长期 Wiki 知识库：`docs/wiki/`，其中 `docs/wiki/voice-validation/` 保存测试方法、断言归因和验证包。

## zhsh / Orion SkillTest Profile 同步规则

- 当前 skill 给 zhsh 平台暴露结构化测试能力的入口文件是根目录 `orion.skilltest.json`，平台优先通过该文件展示“功能模块 -> 测试方案 -> 自然语言用例 -> 执行/证据”。
- 只要新增、删除、重命名或调整可平台化的功能模块、task 示例、执行入口、前置依赖、副作用、风险等级、用例模板或证据字段，必须同步更新 `orion.skilltest.json`，不能只改 `SKILL.md`、README、plan 或 Wiki。
- `orion.skilltest.json` 的 `capabilities` 只放用户可选择的设备/项目测试项；快照、Event Runtime、报告汇总、coverage 等属于 skill 内部技术模块，不是 WS63 项目功能，不能作为独立 capability 暴露给 zhsh，也不要作为用户侧显式 `evidence`/`test_cases` 项展示；如需使用，仅在 skill 内部断言、归因和问题定位链路中保留。
- 基础命令词、在线问答、媒体、云控、烧录等模块如果只是更新语料/命令词表，也要检查 `orion.skilltest.json` 中对应 capability 的 `requires`、`test_cases`、`blocked_conditions` 和 `side_effects` 是否需要同步。
- 更新 `orion.skilltest.json` 后必须至少执行 JSON 解析校验，例如 `python -c "import json; json.load(open('orion.skilltest.json', encoding='utf-8'))"`；如字段结构有调整，还要和 `D:\revolution4s\zhsh\docs\orion-skilltest-profile-framework.md` 的 profile 规范对齐。
- 对 zhsh 展示的能力只写已经有明确方案、用例口径或 runner 入口的模块；缺少真机前置或 oracle 的能力可以暴露为 plan-only/dry-run，但不能在 profile 中伪造 execute PASS 能力。

## 当前支持方向

- 首次唤醒、识别模式下唤醒。
- 半双工、全双工识别。
- 在线全双工 smoke：设备环境切换、在线确认、全双工 API 下发、连续识别/响应断言。
- 基础命令词、需求命令词、自由说小样本。
- 自播前置测量、唤醒打断、命令打断。
- 联网恢复、one-shot、唤醒矩阵、误唤醒、在线 VAD。
- 在线基础命令、音乐、相声、新闻、问答混合压测。
- 误唤醒/误识别记录：额外 wake/ASR/command 都要保留并参与归因。

## 2026-06-01 上线试运行口径

当前 skill 可以上线试运行，但对外报告必须坚持“能力已落地”和“设备功能是否 PASS”分开表达：

- 框架能力：BDD/Task/Adapter/Runtime/快照/报告链路已落地，可稳定输出 PASS/FAIL/BLOCKED/WARN/TIMING_AMBIGUOUS 和证据路径。
- WS63 配置：AP `COM20@921600`、upper/asr `COM17@921600`、control `COM19@115200`，声卡 `VID_8765&PID_5678:9_27F546DA_3_0000`，IoT ID `210006741088068`，UAT `env=1`。
- WB01 配置：AP `COM13@921600`、CP `COM14@921600`、WB01/ASR `COM11@921600`、control `COM12@115200`；WB01 云控异常不阻塞 WS63 试运行。
- 云控前置：所有配置 API 必须先 `check-env`，确认设备端 env 与 `cloud.api_environment` 一致；只看 HTTP 200 或人工印象不合格。
- 快照前置：真机、dry-run、replay、压测都要保留 before/after/diff/checkpoint/final；证据不足使用 UNKNOWN/BLOCKED/EVIDENCE_GAP。
- 断网前置：先确认 DUT 实际联网路径；外部 Wi-Fi 不可控时使用 `BLOCKED_NETWORK_NOT_CONTROLLABLE`，不要用电脑热点状态代替 DUT 断网证据。

WS63 最新专项结论：

- 云控已恢复：真实固件账号/`vir_ver` 已恢复到 `35.03.01.01.18.26.05.04.00.01`，`full-duplex=1`、`mic=on`、`night=off` 最终恢复 PASS。
- command-control 扩大矩阵 12/12 FAIL，root_cause=`command_or_audio_baseline`；半双工 baseline 也 FAIL，不能归因为全双工单点问题。
- online VAD 12/12 FAIL，归因 `wake_precondition_for_online_vad`；offline one-shot 修复 runtime 假阴性后真机复跑 PASS。
- interrupt 当前为 `BLOCKED/TIMING_AMBIGUOUS`，因为注入未稳定命中自播保护窗口；外部 Wi-Fi 断网恢复为 `BLOCKED_NETWORK_NOT_CONTROLLABLE`。
- 最终快照 coverage=1.0，unknown_fields=[]，说明快照框架可用，但不代表所有业务功能 PASS。

## 2026-06-02 WS63 自动烧录口径

- 当前 WS63 串口必须以 `polaris.local.json` 和用户现场确认为准：AP `COM20@921600`、upper/asr `COM17@921600`、control `COM19@115200`。
- `tools/burn/VenusA+WS63/` 是组合烧录工具目录，固件样本位于 `tools/burn/ws63_fw/`；但旧 `COM12/COM13/COM11` 属于 WB01/历史组合拓扑，不能作为当前 WS63 默认烧录串口。
- 默认 WS63-only 入口是 `run.bat` / `run_ws63_only.bat`，当前已改为 control `COM19` + WS63 burn/log `COM17`；如现场确认 BurnTool 应走 AP 口，则显式设置 `WS63_BURN_PORT=COM20` 和 `WS63_VERIFY_PORT=COM20`。
- 完整 VenusA/CSK + WS63 入口是 `run_full_venusa_ws63.bat`，必须显式设置当前真实 `VENUSA_PORT`，脚本不再默认 `COM13`。
- VenusA/CSK 烧录必须解析 `Uart_Burn_Tool` 输出，出现 `CONNECT ROM FAILED`、`HEX SEGMENT x/y FAILED`、`MD5 FORMAT ERROR`、`RESPONSE OVERTIME` 等失败 marker 时立即阻断，不能因进程返回码为 0 继续烧 WS63。
- WS63 烧录必须同时看 BurnTool `optLog_*.txt` 的 `烧写结果：成功` 和设备重启日志里的 `ListenAI Build Info`；BuildInfo 需匹配固件包 `BuildInfo.txt`/`Other/WS63_build_*.log` 中的版本与时间。
- 每次自动烧录默认生成 artifact：`firmware_metadata.json`、console log、WS63 optLog 副本、BuildInfo/Project Version 校验、`burn_summary.json` 和 `burn_summary.md`；没有这些证据不要报告 PASS。
- 2026-06-02 使用旧 `COM12/COM13/COM11` 得到的 WS63 `.00.01`/`.00.02` 烧录结果只归档为历史/组合拓扑证据；当前 WS63 `COM20/COM17/COM19` 已补做 `.00.01` WS63-only、`.00.02` WS63-only、恢复 `.00.01` 和 VenusA/CSK + WS63 full-burn 真机验证，烧录与版本校验均 PASS。
- full-burn 已验证端口组合：VenusA/AP `COM20@3000000/921600`、WS63 BurnTool/verify `COM17@1000000/921600`、control `COM19@115200`；烧录后仍需恢复 `env=1/UAT`、云控基线和基础功能 smoke。
- VenusA 失败阻断逻辑已用真机验证：`HEX SEGMENT 3/5 FAILED` 后脚本退出且不继续进入 WS63 BurnTool；但该动作会真实改写 VenusA flash，曾导致旧 COM13 设备侧持续 `Exception on CORE0 / Illegal instruction`，后续不要把“人为制造 VenusA 失败”当作低风险 smoke。
- 如果 VenusA/CSK 失败后需要恢复，不要盲目反复全包烧录；先检查 boot/reset 硬件时序和专用烧录夹具，再按 `docs/knowledge/venusws63/ws63-auto-burn.md` 留证处理。

## 常用命令

```powershell
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\first_wake.example.json --print-command
python satellite\cucumber-agent-testing\scripts\generate_requirement_package.py --requirement "在线全双工相关功能验证"
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\basic_command.example.json --mode execute --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_full_duplex.example.json --mode dry-run
python satellite\cucumber-agent-testing\scripts\run_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_mixed_stress.example.json --print-command
```

## 配置要点

- WB01：配置 `ap/cp/asr/control` 四个串口。
- WS63：配置 `ap/upper/control` 三个串口，`cp` 留空。
- 真机执行时 managed session 必须使用当前任务/env-file 的串口；不要让根目录 `active_project` 或旧 `config/` 缓存影响另一台设备。
- execute 预检会短暂打开配置中的串口；如果端口被 Xshell/串口助手/旧 logger 占用，应判 `BLOCKED` 并先释放端口，不能把缺日志误判为固件失败。
- 新电脑首次使用声卡前先运行 `python tools\audio\polaris_laid.py ensure`；再用 `python tools\audio\polaris_laid.py list --direction Render` 查询稳定声卡 key。
- 没有单独声卡时，`default_playback_device_key` 留空，使用电脑默认声卡。
- 声卡播放返回 0 但设备无唤醒时，先在控制口执行 `uut-pa.on` 和 `pa-enable.set 0 17 0 1`。
- API/云控场景必须先做“环境门禁”：用 AP/CSK 串口读取并确认设备端 `env` 与 `cloud.api_environment` 一致，确认失败或无法解析时直接 `BLOCKED`，禁止继续调用网络云端配置 API。
- 切换环境后必须按项目要求 `reboot` 并等待设备重新联网，再重新读取 `env`；只发送 `flash.set.int env@1/2/0` 但未复核成功，不能视为环境已正确。
- 云端配置 API 的合格前置顺序固定为：查 `version` -> 查/切 `env` -> 重启/等待上线 -> 复查 `env` -> `deviceinfo` 确认 IoT ID -> 调云控 API -> 检查 HTTP 与业务码 -> 查设备侧 marker/行为。
- 如果设备仍在 PRO/env=0，而本地 `cloud.api_environment=uat/sit`，所有 `set-full-duplex`、`set-volume`、`set-mic`、`set-night-mode`、`set-wakeup-threshold`、主动播报等都必须阻断，不得把失败归因为固件。
- 云控 adapter 必须沿用当前任务/env-file 的项目配置；WB01/WS63 切换时不要让旧 `config/` 或根目录 `active_project` 影响 API 辅助脚本。
- 断网/未联网/联网恢复场景必须先识别 DUT 实际联网路径：`deviceinfo` 查 IP/MAC，再查本机热点客户端；只有 DUT 连本机可控热点时才能用 `hotspot_off/on/cycle`，否则按 `vir_ssid/vir_pwd` 改写重启或外部路由器/AP 控制方案处理，外部 Wi-Fi 不可控时直接 `BLOCKED/SKIPPED`，详见 `docs/knowledge/common/network_disconnect_simulation.md`。

## 持续学习规则

新项目、新功能、新资料不要直接散放到根目录或脚本目录。统一放入：

```text
docs/intake/<project_id>/<YYYYMMDD_topic>/
  learning_manifest.json
  raw/
```

处理顺序：

1. 读取 `learning_manifest.json` 和 `raw/` 原始资料。
2. 先查 `docs/wiki/` 中已有方法和验证包，避免每次从零生成方案。
3. 把通用测试方法、断言公式、失败归因和用例设计思路沉淀到 `docs/wiki/`。
4. 把项目差异、私有日志 marker、配置入口和缺口沉淀到 `docs/knowledge/<project_id>/`。
5. 列出可自动化项、缺口项、需求不明确项。
6. 资料足够且可验证时，才更新 Cucumber feature、reference registry、task example、Runtime profile 或必要工具。
7. 资料不足时只沉淀 gap list，不伪造 PASS/FAIL 逻辑。

## Wiki 使用规则

- 用户只给一句需求时，先匹配 `docs/wiki/voice-validation/test-item-index.md` 和 `docs/wiki/voice-validation/packs/`。
- 生成方案时必须参考对应专题 Wiki：唤醒、命令词、自由说、在线识别、误唤醒。
- 已有验证包优先复用：`first-wake.md`、`recognition-mode-wake.md`、`half-duplex.md`、`online-full-duplex.md`、`basic-command.md`、`online-mixed-stress.md`、`false-wake.md`。
- 在线全双工完整矩阵使用 `satellite/cucumber-agent-testing/references/scenes/online_full_duplex_fd002_fd012.scene.example.json`，单项 task 使用 `online_full_duplex.*.example.json`。
- 输出用例时必须覆盖正例、反例、异常、边界和稳定性；除非用户明确只要 smoke。
- 断言归因必须参考 `docs/wiki/voice-validation/assertion-attribution.md`，不能把环境/资料/时序问题误判为固件问题。
- 新资料学习流程参考 `docs/wiki/voice-validation/new-project-feature-intake.md`；压测/真机异常反哺参考 `docs/wiki/voice-validation/failure-feedback.md`；项目私有 rule/coverage 参考 `docs/wiki/voice-validation/project-rule-overlays.md`。
- 旧 `oldTime/legacy_20260526_144646/satellite/voice-test-plan-designer` 只作为追溯来源；当前工作优先使用 `docs/wiki/`。

## Runtime 扩展约束

- 新功能不要直接堆到一个大脚本里；优先进入 `satellite/cucumber-agent-testing/runtime/plugins/` 对应领域插件。
- 事件统一使用 `ValidationEvent` v1 schema，保留 wall time，但断言以 monotonic timeline 为准。
- Cucumber 只表达测试意图；执行动作、证据解析、断言逻辑必须落到 registry/runtime/tool 层。
- 外部调度、远程设备池、大规模聚类暂不纳入当前 skill，当前优先保证本地真机闭环稳定。

## 当前优化执行入口

- 新增任务优先走 `satellite/cucumber-agent-testing/scripts/run_optimized_task.py`。
- 它会在现有 `run_task.py` 外层生成 `execution_record.json`、`attempts.jsonl`、`adapter_flows/pre.json`、`adapter_flows/post.json`、`state/before.json`、`state/after.json`、`state_diff.json`。
- 执行记录、重试、资源/约束预检优先走 `run_optimized_task.py`；只有调试底层 Cucumber runner 时才直接用 `run_task.py`。
- 需要稳定前置/收尾动作时，把 `execution.adapter_flows.pre/post` 写进 task；`required=true` 的 pre flow 失败应阻断主流程，避免前置问题误判成固件问题。
- 场景生成走 `generate_scene.py`；新场景执行优先走 `run_kernel_scene.py`，只有需要对比旧直接 runner 时才用 `run_scene.py`。
- Replay VM-lite、Simulation-lite、Assertion DSL-lite 分别走 `replay_vm.py`、`simulate_runtime.py`、`run_assertion_dsl.py`。
- Assertion DSL-lite 已支持 `EXPECT_SEQUENCE`、`EXPECT_RESPONSE`、`EXPECT_DURATION`，可表达 ASR/Command 到 TTS/Media 的响应链路和媒体持续时间；复杂业务仍优先固化到 Python profile 断言。
- Adapter/Capability/IR/EventGraph/StateDSL/Trend 分别走 `inspect_device_adapters.py`、`build_capability_matrix.py`、`compile_validation_ir.py`、`build_event_graph.py`、`run_state_assertion_dsl.py`、`build_analytics_trend.py`。
- Validation IR 不再只支持 task：`compile_validation_ir.py` 可用 `--task`、`--scene` 或 `--feature-plan` 编译；`run_kernel_scene.py --emit-ir-bundle` 可输出 scene 级 IR bundle，用于确认 feature/task/scene 最终走同一套 deterministic runtime 输入。
- 新项目接入时先看 `build_capability_matrix.py`，其中 `audio.loopback_oracle`、`media.acoustic_response_oracle`、云控权限和 `reboot.boot_reason_oracle` 为常见缺口；不要把“设备日志说播了”直接等同于“真实出声质量通过”。
- Kernel 生命周期入口走 `run_validation_kernel.py`；它会在 runner 后自动补齐 runtime replay 侧的 event graph、默认 state assertions 和 Replay VM-lite snapshot。
- 状态稳定性不要只看最终 PASS/FAIL；必须结合 `runtime_state.json` 里的 `state_health`、`state_violations`、`coverage` 区分崩溃/重启、日志缺口、媒体顺序缺失和业务断言失败。
- `run_state_coverage_policy.py` 和 Kernel 后处理会按 profile 检查 coverage 阈值；缺少首唤醒 WakeDetected、基础命令 ASR/Command、联网恢复 NetworkLost/NetworkRecovered 等关键覆盖时，应先归因日志/前置/需求，再决定是否判固件问题。
- 项目差异优先写到 `state_assertion_policy.json` 的 `coverage.projects.<project_id>`，不要在代码里硬编码 WB01/WS63 或新项目阈值。
- Event Graph 需要优先查看 `risk_summary` 和因果边：`command/asr_to_*_response`、`media_started_to_completed`、`media_interrupted`、`interrupt_to_recognition`、`possible_reboot/crash_after_activity`，用于分析在线媒体、打断和重启根因。
- 项目私有云端/媒体/TTS/MP3 marker 先沉淀到 `references/optimization/event_graph_rules.json` 或通过 `build_event_graph.py --rules` 加载，不要优先写死到核心 `runtime/event_graph.py`。
- adapter 单动作规划/执行入口走 `run_adapter_action.py`，默认只 dry-run 渲染命令；常见多步前置走 `plan_adapter_flow.py`，例如 `pa_recover`、`switch_device_env`、`wake_audio_file`、`set_volume`、`set_half_duplex`、`set_full_duplex`。声卡查询/安装动作是 `audio.playback/laid_check`、`audio.playback/laid_install`、`audio.playback/laid_list`、`audio.playback/ensure_laid`；安装脚本固定在 `tools/audio/laid/`。真执行副作用必须显式 `--execute --allow-side-effects`。
- 首次唤醒时序不要直接拿播放进程启动当唯一锚点；如播放进程明显长于 wav 时长，优先按 `AudioCompleted - audio_duration_ms` 估算有效波形起点，无法估算才输出 `TIMING_AMBIGUOUS`。

## 2026-05-27 新增落地规则

- L1 功能必须优先复用 `tasks/examples/` 中的标准 task；批量能力可走 `references/scenes/l1_voice_core_supported_smoke.scene.example.json`。
- 用户给新需求时，先用 `generate_requirement_package.py` 生成 `test_plan.md`、`case_matrix.md`、`gap_list.md`、`confirmation.md`、`run_plan.json`，确认后再 execute。
- 真机失败后不要只口头分析，优先执行 `generate_failure_case.py --run <run>`，把失败转成候选回归用例、断言补强建议和复测清单。
- 候选失败用例不能直接落库；必须经人工确认后执行 `register_failure_case.py --package <failure_case_package.json> --approve --approved-by <name>`，再写入 `failure_regression_registry.json`、`tasks/generated/regression/`、`generated_failure_regression.scene.example.json` 和 failure-pattern wiki。
- 在线媒体/TTS/MP3 响应必须至少跑日志级 `analyze_media_response_oracle.py`；没有 loopback/capture 时只能说“日志显示播报链路”，不能说“真实声学播放通过”。
- 需要证明真实出声时使用 `tools/audio/polaris_acoustic_oracle.py`：先 `probe` 查回采设备，再 `record` 或 `analyze --audio-file <capture.wav>`，报告 RMS、峰值、有效时长和削波；依赖或设备缺失时必须判 `BLOCKED`。
- `build_validation_summary_report.py` 是总报告入口，会汇总 BDD、Runtime、Event Graph、媒体 oracle、重启/崩溃和未通过项。
- WB01/WS63 项目私有 Event Graph rule 和 coverage 阈值在 `references/optimization/event_graph_rules.json`、`state_assertion_policy.json` 中维护；新项目不要硬编码到脚本里。

## 2026-05-28 真机闭环补充

- 执行前置串口/云控/联网 Adapter 时，必须优先使用当前任务传入的 `--env-file`；涉及串口直接写入时使用 `--no-sync-config`，避免项目串口互相污染。
- 云控设置不只看 HTTP 状态码，还要看业务返回码；设备未上线、环境不一致、业务码非 0/200 时应归为 `BLOCKED` 或环境问题，不能写成 PASS。
- 2026-06-01 后新增硬规则：云控 API 前必须由工具实际解析到设备端 `env` 与 `cloud.api_environment` 一致；如果只查询失败、只相信配置文件、只相信人工印象或只完成切换命令但未复查，都不能继续调用云端配置 API。
- WS63 云控失败先按版本/环境/在线态排查：`version` 中 `Project Version=35.03.01.01.18.26.05.04.00.02` 属于已知后台未授权 API 控制版本，应切到 `35.03.01.01.18.26.05.04.00.01`，再确认 `env=1`(UAT) 或 `env=2`(SIT) 与 `cloud.api_environment` 一致；诊断工具为 `tools/cloud/polaris_cloud_diagnostics.py`，知识文档为 `docs/knowledge/venusws63/cloud-control-version-gate.md`。
- Cucumber 子进程通过 `POLARIS_ENV_FILE` 继承项目配置；新增项目时要保证该配置文件包含串口、声卡、UAT/SIT 和基础网络字段。
- 长 scene 可使用 `--max-retries N --retry-blocked` 处理声卡、语音识别或云端瞬态阻塞；重试后仍失败才进入 failure-to-test-case 反哺。
- 全双工断言应区分 setup/recovery 与主流程，不把前置联网恢复重启误判为固件重启；顺序断言使用有效事件对而不是全局第一个噪声事件。
- 2026-06-01 后新增断网硬规则：不能把“电脑热点关闭”或“电脑 Wi-Fi 关闭”直接等同于 DUT 断网；必须有 DUT MAC 挂在该热点、或有 `vir_ssid/vir_pwd` 写入重启、或有外部路由器/AP 控制与设备侧离线 marker。证据不足时输出 `BLOCKED_NETWORK_NOT_CONTROLLABLE` 或 `EVIDENCE_GAP`。


## 后续优化建议

- 定期执行 `python satellite\cucumber-agent-testing\scripts\run_regression_suite.py`，覆盖 parser、snapshot、runtime 和关键 smoke。
- 在线请求需要做耗时统计时，使用 `python satellite\cucumber-agent-testing\scripts\build_latency_summary_report.py --input <online_request_ids.json>`。
- 出现 evidence gap 时，先补项目私有 TTS/media/DeviceControl marker，再决定是否注册为 failure regression。
- 扩大样本前先跑小样本 smoke；小样本明确 PASS/BLOCKED/FAIL 后再进入长时压测。
- TTS 云端凭据不得写死到仓库；如需讯飞 TTS，使用 `POLARIS_XFYUN_APP_ID`、`POLARIS_XFYUN_API_KEY`、`POLARIS_XFYUN_AUTH_ID` 或兼容的 `XFYUN_*` 环境变量，未配置时回退本机 SAPI。
