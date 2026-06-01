# Polaris 环境与迁移说明

本文档统一使用 UTF-8 编码，用来说明当前 skill 所依赖的环境、默认值，以及换设备或换主机后需要先做哪些准备。

## 1. 主机环境要求

### 1.1 操作系统

- Windows 主机
- 可用 PowerShell
- 支持 Windows 热点相关接口

原因：`tools/device/polaris_network_orchestrator.py` 依赖 Windows 热点能力。

### 1.2 Python 与依赖包

当前工作流依赖这些 Python 包：

- `requests`
- `pyserial`
- `openpyxl`
- `websockets`
- `pyyaml`

### 1.3 外部工具与资产

- `ffmpeg`
- `listenai-play` 播放脚本
- 可用的本地 TTS 能力
  - 优先走项目内 TTS 构建路径
  - Windows 上可回退到 SAPI

## 2. 设备与连接要求

### 2.1 串口拓扑

当前常用拓扑：

- WB01：AP `COM13@921600`、CP `COM14@921600`、WB01/ASR `COM11@921600`、control `COM12@115200`。
- WS63：AP `COM20@921600`、upper/asr `COM17@921600`、control `COM19@115200`，无 CP。

本机基础配置统一写在：

- `polaris.local.json`（主入口，按 `active_project` 区分项目）
- `config/polaris_local_ports.json`（旧版兼容缓存）

规则：

- 工具命令未显式指定串口时，优先读取 `polaris.local.json` 当前项目配置。
- 工具命令显式指定串口时，会把该角色的新串口同步回 `polaris.local.json`，并同步旧版缓存。
- `config/polaris_env.json` 保留为历史兼容兜底；新项目不要继续把基础配置堆到 `config/`。

如果换机器后 COM 号变了，优先修这里：

- 查看当前配置：`python tools/core/polaris_config.py show`
- 手动同步角色：`python tools/core/polaris_config.py set --role ap --port COM20`
- 手动同步控制串口：`python tools/core/polaris_config.py set --role control --port COM19`
- 直接执行带显式串口的命令，例如 `python tools/device/polaris_power_control.py cycle --target asr --port COM19`
- 或手动编辑 `polaris.local.json`

兼容规则：老脚本里写死的 `COM12/COM13/COM14` 不再直接视为物理端口，而是按 `cp/asr/ap` 角色映射到本地配置中的实际端口；WS63 这类无 CP 项目会跳过空 `cp` 端口。如果确实要向某个物理端口直发，使用支持 `--port` 的工具显式指定。

注意：串口 logger 启动时会读取一次本地配置；如果运行中改了 COM 映射，需要停止并重启 `polaris_serial_harness.py start` 后新映射才会进入采集线程。

### 2.2 播放链路

- 主机必须有一个能稳定播放到 DUT 麦克风的输出设备。
- 播放设备建议配置稳定 `device_key`，并能被 `listenai-play` 正常识别；如果项目/设备没有单独写声卡 key，则使用电脑默认播放声卡。

当前默认值来自：

- `polaris.local.json -> common.audio.default_playback_device_key` 或项目自己的 `audio.default_playback_device_key`

该字段留空或不存在时，播放命令会省略 `--device-key`，由 `listenai-play` 绑定系统默认输出设备。多声卡机器上如果出现“播放返回 0 但设备无唤醒”，先确认系统默认声卡是否正确。

### 2.3 设备身份信息

当前流程依赖 AP `deviceinfo` 中的这些字段：

- `iot_id`
- `mac`
- `wakeup_id`

这些字段用于：

- 云端控制请求
- 结果同步
- 设备身份确认

补充说明：

- 如果重启后 `deviceinfo` 临时只回部分字段，当前脚本会优先通过 `deviceinfo` 自动读取；旧版脚本才回退使用 `config/polaris_env.json -> current_deviceinfo`，不会再因此阻断云控。

## 3. 仓库内必须保留的数据

迁移 skill 时，以下内容建议一起带走：

- `SKILL.md`
- `docs/skill/capabilities-and-usage.md`
- `docs/skill/environment-and-migration.md`
- `polaris.local.example.json`
- `docs/skill/*.md`
- `docs/api/common_request.py`
- `docs/cases/*.xlsx`
- 历史 tone/reference 如需追溯在 `oldTime/` 中查找，不再放当前根目录
- Cucumber 用例：`satellite/cucumber-agent-testing/features/*.feature`
- Cucumber 任务：`satellite/cucumber-agent-testing/tasks/**/*.json`
- `tools/**/*.py`

## 4. 当前默认配置

以当前仓库为准：

- 热点 SSID：从 `polaris.local.json`、命令行参数或 `POLARIS_LOCAL_HOTSPOT_SSID` 读取，不在仓库写死。
- 热点密码：从 `polaris.local.json`、命令行参数或 `POLARIS_LOCAL_HOTSPOT_PASSWORD` 读取，不在仓库写死。
- 云环境：`SIT`
- 默认唤醒词：`小美小美`
- 默认播放设备 key：WS63 当前为 `VID_8765&PID_5678:9_27F546DA_3_0000`；WB01 当前沿用 `VID_8765&PID_5678:9_2A847557_7_0000`
- 当前设备：
  - WS63：`iot_id=210006741088068`，`mac=60:7A:D8:1A:67:26`，最近 IP `192.168.2.5`
  - WB01：按当前接入设备通过 `deviceinfo` 实时读取，不在模板中写死

## 5. 新设备 bootstrap 顺序

换 DUT 后，建议严格按下面顺序做：

### 第 1 步：新建 session

```powershell
$ts = Get-Date -Format yyyyMMddHHmmss
New-Item -ItemType Directory -Path ("result\$ts") -Force | Out-Null
Set-Content .current_result_dir (Resolve-Path ("result\$ts")).Path -Encoding ASCII
python tools/device/polaris_serial_harness.py start --session-dir ("result\$ts")
```

### 第 2 步：确认串口映射

至少执行一次：

```powershell
$session = Get-Content .current_result_dir
python tools/device/polaris_serial_harness.py send --session-dir $session --role ap --command version
python tools/device/polaris_serial_harness.py send --session-dir $session --role asr --command "listen version"
```

未指定 `--port` 时，`send` 默认按 `--role` 读取本地配置：

```powershell
python tools/device/polaris_serial_harness.py send --session-dir $session --role ap --command version
python tools/device/polaris_serial_harness.py send --session-dir $session --role asr --command "listen version"
```

### 第 3 步：确认播放链路

- 检查 `default_playback_device_key` 是否还是正确设备；如果留空，则检查电脑默认播放设备是否正确。
- 执行一次唤醒探测，确认 DUT 能真正听到播放音频。

### 第 4 步：采集设备身份

```powershell
python tools/cloud/polaris_app_control.py probe-device
python tools/probe/polaris_state_probe.py snapshot --label bootstrap
```

至少确认：

- `iot_id`
- `mac`
- `wakeup_id`
- 当前热点 IP

### 第 5 步：确认网络链路

```powershell
python tools/device/polaris_network_orchestrator.py hotspot-status
```

必要时继续跑：

```powershell
python tools/device/polaris_network_orchestrator.py hotspot-cycle
python tools/device/polaris_network_orchestrator.py vir-reboot --ssid <wifi_ssid> --pwd <wifi_password>
```

### 第 6 步：先做最小冒烟

```powershell
python tools/probe/polaris_phrase_probe.py --text 小美小美 --observe-ms 15000 --label bootstrap_wake
python tools/probe/polaris_phrase_probe.py --text 小美小美 --text 打开空调 --observe-ms 15000 --label bootstrap_wake_cmd
python tools/cloud/polaris_app_control.py set-volume --value 20
python tools/cloud/polaris_app_control.py set-mic --enable 1
```

### 第 7 步：再扩到用例与报告

```powershell
python tools/reporting/polaris_doc_case_audit.py
python tools/execution/polaris_doc_case_runner.py --case-id 美的空调_22 --device-key "YOUR_DEVICE_KEY"
python tools/reporting/polaris_status_sync.py
```

## 6. 换机器时优先检查的地方

### 必改或必核对

- `config/polaris_env.json`
  - `default_playback_device_key`
  - `current_env_label`
  - `current_deviceinfo`
- `tools/device/polaris_serial_harness.py`
  - 串口映射
- `tools/device/polaris_power_control.py`
  - 控制口是否变化（当前 WB01 `COM12`、WS63 `COM19`）
- `tools/execution/polaris_case_runner.py`
  - `listenai-play` 路径
- `tools/audio/polaris_audio_builder.py`
  - TTS 环境变量是否配置；仓库不保存云端 TTS 密钥，未配置时回退 SAPI

## 7. 当前 skill 的迁移边界

满足下面条件时，通常可以直接复用这套 skill：

- 串口拓扑仍是 Polaris 风格
- 云端接口仍兼容当前 `common_request.py`
- 音频播放仍通过 `listenai-play` 或兼容入口完成
- 网络编排仍基于 Windows 热点

以下情况通常需要改脚本再继续：

- COM 号整体变化
- 播放路径变化
- 热点机制变化
- 云端接口或 payload 变化
- 设备型号能力边界明显不同

## 8. 当前不建议在新设备上默认继承的能力

这些能力即使脚本入口还在，也不要当作“默认可复用能力”：

- `set-character-value`
- `set-log`
- 自定义唤醒词本地生效

建议在新设备上重新验证通过后，再把它们加入主 skill。

