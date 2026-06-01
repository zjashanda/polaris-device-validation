# 在线全双工功能验证流程

本文沉淀“在线全双工相关功能验证”的标准流程，后续用户只要换项目配置、唤醒词、超时时间或语料，执行逻辑和断言口径不需要临时依赖大模型改脚本。

## 1. 功能意图

在线全双工不是单纯“能识别一句话”，而是验证设备在联网状态下切到全双工/自然对话模式后，能在一次唤醒后的识别窗口内继续接收语音输入，并对在线 ASR、云端 TTS/media 或命令响应形成闭环。

需要区分三类能力：

- 连续识别：唤醒后连续说在线问答、命令或自由说，设备应继续识别。
- 播报中识别/打断：设备自播、TTS 或媒体播放时，按需求允许识别或打断时才应响应。
- 超时退出：超过 full_duplex_timeout_s 后应退出识别态，超时临界区需要保留时间线，不能粗暴判 FAIL。

## 2. 前置条件

- `polaris.local.json` 已选择正确 `active_project`，例如 `cskwb01` 或 `venusws63`。
- 串口拓扑正确：
  - WB01：AP + CP + ASR/WB01 + control。
  - WS63：AP + upper/ASR + control，CP 为空。
- 设备已联网，云端 API 环境和设备端环境一致：
  - UAT 项目：AP/CSK 侧先进入 UAT，再调用 UAT API。
  - SIT 项目：AP/CSK 侧先进入 SIT，再调用 SIT API。
  - 必须由工具复查到设备端 `env` 与 `cloud.api_environment` 一致；查询失败、设备仍在 PRO/env=0 或切换后未重启/复查成功，都应先 `BLOCKED`，不能调用全双工/半双工云控 API。
- 声卡可播放；如果项目未单独配置 `default_playback_device_key`，默认使用电脑默认播放设备。
- 如果声卡播放返回成功但设备无唤醒证据，先单独执行 PA 恢复：

```powershell
python satellite\cucumber-agent-testing\scripts\plan_adapter_flow.py --flow pa_recover --env-file polaris.local.json --execute --allow-side-effects
```

PA 命令只能发到 control 串口，不能发到 AP/CP/ASR。

## 3. 标准执行链路

当前已沉淀任务：

```text
satellite/cucumber-agent-testing/tasks/examples/online_full_duplex.example.json
```

推荐入口：

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_full_duplex.example.json --mode execute --allow-side-effects --manage-session --runtime-strict
```

执行过程：

1. `laid_check`：检查本机声卡查询工具是否可用。
2. `switch_device_env`：按项目配置向 AP 串口发送 UAT/SIT 环境切换命令；如果项目要求重启，必须重启并等待联网。
3. `cloud_env_gate`：复查 AP/CSK 侧 env 与 `cloud.api_environment` 一致，未确认则阻断云控 API。
4. `ensure_online`：尽量确认本机可控 Wi-Fi/热点与设备在线状态；如果设备已通过外部网络在线但不受本机热点控制，本步骤可作为弱前置。需要模拟断网时先按 `docs/knowledge/common/network_disconnect_simulation.md` 判断：本机热点可控、`vir_ssid/vir_pwd` 改写、外部路由器/AP 控制，或直接 BLOCKED/SKIPPED。
5. `set_full_duplex`：调用云控/API 下发全双工，timeout 使用 task 或 `polaris.local.json` 里的 `full_duplex_timeout_s`。
6. Cucumber 执行 `@full_duplex_recognition`：通过固定 mapping 执行全双工 doc case/语音链路。
7. Runtime replay：解析日志和产物，生成事件、状态机、事件图、断言结果。

默认不自动恢复半双工，避免覆盖调试状态；如需恢复：

```powershell
python satellite\cucumber-agent-testing\scripts\plan_adapter_flow.py --flow set_half_duplex --env-file polaris.local.json --execute --allow-side-effects
```

## 4. Cucumber 用例口径

已有 feature 场景：

```gherkin
@P0 @duplex @full_duplex_recognition
场景: 全双工识别
  假如 云端或本地配置已切换为全双工
  当 播放唤醒词并在全双工识别窗口内连续播放命令词
  那么 应观察到符合全双工预期的 ASR 和命令闭环
  而且 播报中可识别、可打断或可连续对话需要按需求口径断言
```

后续如果用户写新的自然语言用例，只要步骤语义仍然命中 step registry 或先补充 registry，就可以脱离大模型执行。

## 5. 断言口径

强断言：

- `cloud_full_duplex_apply`：云控/API 返回成功，并且设备日志出现全双工配置应用证据。
- `duplex_mode_timeout_evidence`：设备日志中出现 full-duplex timeout 或 algo timeout 刷新值。
- `wake_before_command`：命令/在线语料前必须有有效唤醒证据。
- `command_asr_or_keyword`：识别窗口内出现 ASR 文本、命令关键词或在线 ASR 证据。
- `duplex_successful_response`：至少出现一次云端 TTS、media、命令响应或在线响应证据。
- `no_reboot_in_duplex_recognition` / `no_crash_in_duplex_recognition`：执行窗口内无 reboot、watchdog、panic、assert、hardfault 等异常。

辅助断言：

- 识别态是否在 `full_duplex_timeout_s` 内保持。
- 播报中输入是否命中安全注入窗口。
- 是否出现未播放语料对应的额外 ASR/命令识别；这类要记录为误识别/误唤醒候选。

## 6. 归因规则

| 现象 | 结论 |
| --- | --- |
| 声卡 key 不存在、播放命令失败 | `BLOCKED`，测试环境/声卡问题 |
| 串口无日志或 logger 未启动 | `BLOCKED`，串口/logger 问题 |
| 设备未联网或云端环境不一致 | `BLOCKED`，联网/云端/API 环境问题 |
| set_full_duplex API 成功但设备日志无应用证据 | `BLOCKED` 或 `FAIL`，先按云控/设备侧配置链路复核 |
| 已唤醒但无 ASR/命令/在线响应 | `FAIL`，ASR/固件/云端识别链路问题 |
| 播报中是否应该响应不明确 | `REQUIREMENT_REVIEW`，需求口径问题 |
| 输入落在唤醒播报、TTS 播放或超时临界区 | `TIMING_AMBIGUOUS`，时序不明确，不直接判固件问题 |
| 出现 reboot/crash/watchdog | 独立归设备稳定性问题，不和识别率混算 |

## 7. 项目差异

- `cskwb01`：按 AP/CP/ASR 三端证据断言。API 前先确保 CSK/AP 端环境与 `cloud.api_environment` 一致。
- `venusws63`：无 CP，按 AP + upper/ASR 证据断言。已知 UAT v1 `fullDuplex` 接口有设备侧应用证据，优先使用 v1。
- 新项目：先补 `polaris.local.json` 的串口、声卡、Wi-Fi、cloud 环境和 `device_env_command`，再执行 dry-run/precheck。

## 8. 调试命令

只做前置检查：

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_full_duplex.example.json --precheck-only
```

只打印命令：

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_full_duplex.example.json --print-command
```

不碰真机的 dry-run：

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_full_duplex.example.json --mode dry-run
```

真机执行：

```powershell
python satellite\cucumber-agent-testing\scripts\run_optimized_task.py --task satellite\cucumber-agent-testing\tasks\examples\online_full_duplex.example.json --mode execute --allow-side-effects --manage-session --runtime-strict
```

## 9. 2026-06-01 WS63 实测补充

最新 WS63 专项验证说明：云控可成功下发全双工，不等于全双工语音命令闭环已经 PASS。

- 前置：WS63 `check-env` PASS，env=`1/UAT`，版本/`vir_ver` 为 `35.03.01.01.18.26.05.04.00.01`，`set-full-duplex --enable 1` 可 HTTP/业务码 PASS。
- 对照：半双工 baseline 也 FAIL，command-control 扩大矩阵 12/12 FAIL，root_cause=`command_or_audio_baseline`。
- 结论：当前 WS63 的命令闭环问题不能归因为“全双工单点失败”；必须按 wake/audio baseline、ASR/命令域、TTS/media、oracle 缺口分层定位。
- 变体：continuous/media/exception 仍 FAIL 在命令闭环；timeout_boundary 因 voice preset 阻塞；random stress 小样本可 PASS 但不能覆盖命令矩阵失败。
- 报告口径：云控 apply PASS 只能作为配置前置成功；最终行为 PASS 仍需 wake -> ASR -> command/response -> TTS/media/状态闭环证据。
