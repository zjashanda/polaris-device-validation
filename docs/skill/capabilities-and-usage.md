# Polaris 能力与用法

本文档统一使用 UTF-8 编码，只记录当前已经验证为“正常可控”或“可稳定执行”的入口。

## 1. 当前保留能力

### 1.0 2026-06-01 上线试运行基线

- WS63 当前映射：AP `COM20@921600`、upper/asr `COM17@921600`、control `COM19@115200`，声卡 `VID_8765&PID_5678:9_27F546DA_3_0000`。
- WB01 当前映射：AP `COM13@921600`、CP `COM14@921600`、WB01/ASR `COM11@921600`、control `COM12@115200`。
- 云控配置能力可用前提：先 `check-env`，确认设备端 env 与 `cloud.api_environment` 一致；否则直接 BLOCKED。
- 快照能力已落地：before/after/diff/checkpoint/final、coverage、unknown_fields 和归因提示均进入报告。
- 断网能力按实际联网路径选择；外部 Wi-Fi 不可控时用 `BLOCKED_NETWORK_NOT_CONTROLLABLE`。

### 1.1 基础设施与观测

- 持续串口日志
  - WB01：`COM13`=AP、`COM14`=CP、`COM11`=WB01/ASR
  - WS63：`COM20`=AP、`COM17`=upper/asr
- 串口命令下发与回显
- 当前控制口：WB01 `COM12@115200`，WS63 `COM19@115200`
- 状态快照与 diff
- 短语播放 + 日志关联
- 热点状态查询、热点重启
- `vir_ssid / vir_pwd` 改写后重启验证

### 1.2 当前已验证正常的云端控制

- `probe-device`
- `set-full-duplex`
- `set-volume`
- `set-multi-wakeup`
- `set-accent`
- `set-wakeup-threshold`
- `set-mic`
- `set-night-mode`
- `set-wakeup-audio-upload`
- `proactive-interaction`

### 1.3 当前已验证可执行的自动化入口

- 单条文档用例：`tools/execution/polaris_doc_case_runner.py`
- suite 批量执行：`tools/execution/polaris_batch_runner.py`
- audit：`tools/reporting/polaris_doc_case_audit.py`
- 结果同步：`tools/reporting/polaris_status_sync.py`
- 结果表导出：`tools/reporting/polaris_export_case_table.py`
- FAIL 明细导出：`tools/reporting/export_fail_case_detail_md.py`
- 自动化明细导出：`tools/reporting/export_auto_case_detail_md.py`

## 2. 常用命令入口

### 2.1 启动 live session

```powershell
$ts = Get-Date -Format yyyyMMddHHmmss
New-Item -ItemType Directory -Path ("result\$ts") -Force | Out-Null
Set-Content .current_result_dir (Resolve-Path ("result\$ts")).Path -Encoding ASCII
python tools/device/polaris_serial_harness.py start --session-dir ("result\$ts")
```

### 2.2 串口直发与状态探测

```powershell
$session = Get-Content .current_result_dir
python tools/device/polaris_serial_harness.py send --session-dir $session --role ap --command version
python tools/device/polaris_serial_harness.py send --session-dir $session --role asr --command "listen version"
python tools/probe/polaris_state_probe.py snapshot --label smoke
```

串口默认优先从根目录 `polaris.local.json` 的当前 `active_project` 读取；显式传入 `--port` 时会同步到根配置和旧版 `config/polaris_local_ports.json` 缓存。也可以先查看或手动同步配置：

```powershell
python tools/core/polaris_config.py show
python tools/core/polaris_config.py set --role ap --port COM20
python tools/core/polaris_config.py set --role asr --port COM17
```

推荐按角色发送，避免换机器后改命令：

```powershell
python tools/device/polaris_serial_harness.py send --session-dir $session --role ap --command version
python tools/device/polaris_serial_harness.py send --session-dir $session --role asr --command "listen version"
```

旧工具里仍写死的 `COM12/COM13/COM14` 会在 `tools/core/polaris_runtime.py` 中按 `cp/asr/ap` 角色映射到本地配置里的实际端口；无 CP 项目会跳过空 `cp` 端口。

### 2.3 短语探测

默认播放设备来自 `polaris.local.json -> common.audio.default_playback_device_key` 或项目自己的 `audio.default_playback_device_key`，旧版 `config/polaris_env.json` 只作为兜底。该字段留空或不存在时，脚本会省略 `--device-key`，由 `listenai-play` 使用电脑默认播放声卡。

```powershell
python tools/probe/polaris_phrase_probe.py --text 小美小美 --observe-ms 15000 --label wake_smoke
python tools/probe/polaris_phrase_probe.py --text 小美小美 --text 打开空调 --observe-ms 15000 --label wake_cmd_smoke
```

如果播放返回码为 `0`，但 CP/AP/ASR 都没有唤醒证据，先按声卡/PA 链路问题处理。WB01/WS63 类项目可在控制口执行：

```text
uut-pa.on
pa-enable.set 0 17 0 1
```

执行后复播唤醒词；若唤醒恢复，归因为 PA/声卡链路前置缺失，不直接判固件失败。

### 2.4 电源与网络

```powershell
python tools/device/polaris_power_control.py cycle --target asr
python tools/device/polaris_power_control.py cycle --target csk
python tools/device/polaris_network_orchestrator.py hotspot-status
python tools/device/polaris_network_orchestrator.py hotspot-set --enable 0
python tools/device/polaris_network_orchestrator.py hotspot-set --enable 1
python tools/device/polaris_network_orchestrator.py hotspot-cycle
python tools/device/polaris_network_orchestrator.py vir-reboot --ssid <wifi_ssid> --pwd <wifi_password>
```

`polaris_power_control.py` 未指定 `--port` 时读取配置里的 `control` 串口；指定 `--port COMxx` 时会同步 `control=COMxx` 到 `polaris.local.json` 和旧版串口缓存。

断网/未联网模拟必须先识别 DUT 实际联网路径：

- DUT 连本机热点：使用 `hotspot-set --enable 0/1` 或 `hotspot-cycle`。
- DUT 支持 `vir_ssid/vir_pwd`：写错 SSID/密码后重启模拟未联网，写回正确配置后重启恢复。
- DUT 连外部 Wi-Fi：需要外部路由器/AP 的电源、SSID、黑名单/ACL、防火墙等控制能力；没有控制能力时只做在线验证，断网/联网恢复标记 `BLOCKED/SKIPPED`。
- 具体判断口径见 `docs/knowledge/common/network_disconnect_simulation.md`。

### 2.5 云端控制

```powershell
python tools/cloud/polaris_app_control.py check-env
python tools/cloud/polaris_app_control.py probe-device
python tools/cloud/polaris_app_control.py set-full-duplex --enable 1 --timeout 15
python tools/cloud/polaris_app_control.py set-volume --value 20
python tools/cloud/polaris_app_control.py set-multi-wakeup --enable 0
python tools/cloud/polaris_app_control.py set-accent --accent-id cantonese --enable-accent 0 --mixed-res-enable 0
python tools/cloud/polaris_app_control.py set-wakeup-threshold --threshold 75
python tools/cloud/polaris_app_control.py set-mic --enable 1
python tools/cloud/polaris_app_control.py set-night-mode --enable 0 --time-from 23:00 --time-to 07:00 --volume 10 --awake-threshold 0
python tools/cloud/polaris_app_control.py set-wakeup-audio-upload --enable 0
python tools/cloud/polaris_app_control.py proactive-interaction --interrupt --end-session
```

`check-env` 是所有网络云控配置 API 的强制前置；未解析到 env、设备端 env 与 API 环境不一致、或设备仍在 PRO/env=0 时，后续云控动作必须直接 BLOCKED。

### 2.6 文档用例与报告

```powershell
python tools/execution/polaris_doc_case_runner.py --case-id 美的空调_22 --device-key "YOUR_DEVICE_KEY"
python tools/execution/polaris_batch_runner.py --suite-file spec\suites\offline_smoke.yaml --device-key "YOUR_DEVICE_KEY"
python tools/reporting/polaris_doc_case_audit.py
python tools/reporting/polaris_status_sync.py
python tools/reporting/polaris_export_case_table.py --executed-only
python tools/reporting/export_fail_case_detail_md.py
python tools/reporting/export_auto_case_detail_md.py
```

## 3. 本轮已验证结果

本轮基线 session：`result/20260423111046`

### 3.1 基础设施

已实际跑通：

- 串口 logger、串口直发、state probe、phrase probe
- `asr` 掉电重启
- `csk` 掉电重启
- 热点状态查询
- 热点关闭再打开
- `vir_ssid / vir_pwd` 改写后重启并重新上线
- 按 DUT 实际联网路径选择断网模拟方案：本机热点、`vir_ssid/vir_pwd`、外部路由器/AP 控制或 BLOCKED/SKIPPED 弱前置

### 3.2 有本地读回或明确断言的正常控制

- 音量：`30 -> 20`
  - 最终 ASR 回读正常
- 唤醒阈值：`80 -> 75`
  - AP 回读正常
- Mic 开关
  - 关 mic 后探测结果为 `cp wake=1, ap wake=0`
  - 开 mic 恢复后探测结果为 `cp/ap wake=1/1`
- 默认唤醒词恢复
  - 切回 `小美小美` 后探测恢复正常唤醒

### 3.3 云端调用成功的控制项

以下动作已在当前 DUT 上返回业务成功，并保留了本地证据：

- 自然对话开关
- 多设备唤醒开关
- 方言开关
- 夜间模式开关
- 唤醒音频上传开关
- 主动交互

### 3.4 文档与报告链路

已实际执行：

- `polaris_doc_case_audit.py`
- `polaris_doc_case_runner.py`
- `polaris_batch_runner.py`
- `polaris_status_sync.py`
- `polaris_export_case_table.py`
- `export_fail_case_detail_md.py`
- `polaris_refresh_failure_diagnosis.py`
- `export_auto_case_detail_md.py`

最新状态汇总：`90 executed / 81 PASS / 3 FAIL / 6 BLOCKED / 625 SKIP`

## 4. 当前明确排除项

以下项目当前不作为“正常控制”能力：

- `set-character-value`
  - 当前返回 `code=501`。
- `set-log`
  - 云端 success，但本地 `log_lev` 未随 `level=2` 发生可验证变化。
- 自定义唤醒词
  - `客厅空调` 云端设置成功，但当前 CA3X 设备本地探测仍为 `0 wake`。

## 5. 需要记住的边界

- 用例 runner 能执行，不代表用例结果一定 PASS；最终判定仍依赖当前 DUT 行为。
- 当前 `美的空调_22` 已能正常跑完，但结果是 FAIL，原因是 `successful_response_count=0`，这属于后续专项收口问题，不影响 skill 主能力使用。
- 若后续重新验证 `set-log`、自定义唤醒词或音色切换通过，再补回本文件即可。

## 6. 模块化验证池入口

当前已新增 Polaris 专用模块化验证池，用来沉淀唤醒、超时、全/半双工、联网、云控设置、夜间模式、音量、空调命令词、在线/离线 ASR 和 FAIL 收敛方法。

常用入口：

```powershell
python tools/pool/polaris_validation_pool.py validate
python tools/pool/polaris_validation_pool.py classify --project-key polaris_midea_ac --out outputs\polaris_pool_match.md SKILL.md docs/skill/capabilities-and-usage.md docs/skill/environment-and-migration.md references
python tools/suite/run_polaris_formal_suite.py --tag plan_only
```

读取顺序：

1. `references/modular-validation-workflow.md`
2. `references/evidence-rules.md`
3. `references/validation-pool/INDEX.md`
4. 命中的具体模块，例如 `wake-session.md`、`duplex-mode.md`、`network-online.md`
5. `references/project-profiles/polaris_midea_ac.json`

默认原则：raw FAIL 先收敛验证路径；只有前置满足、采集有效、需求明确且行为矛盾时，才保留最终固件 FAIL。

