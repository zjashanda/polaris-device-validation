# 配置说明

本文说明 clone 仓库后需要配置哪些文件、每个参数是什么意思、什么时候必须填写。

## 文件分层

| 文件 | 是否提交 Git | 用途 |
| --- | --- | --- |
| `polaris.local.example.json` | 是 | 根目录本机配置模板，新用户只需要复制它。 |
| `polaris.local.json` | 否 | 每台电脑自己的真实配置，记录当前项目、串口、声卡、Wi-Fi、唤醒词、云环境。 |
| `satellite/cucumber-agent-testing/tasks/examples/*.json` | 是 | 某个功能的触发任务示例，默认读取 `polaris.local.json`。 |
| `satellite/cucumber-agent-testing/tasks/templates/new_feature.template.json` | 是 | 新功能接入前的沉淀模板。 |
| `satellite/cucumber-agent-testing/references/voice_core_mapping.json` | 是 | 已沉淀功能的执行脚本、断言和归因规则。 |
| `satellite/cucumber-agent-testing/features/polaris_voice_core.feature` | 是 | Cucumber 自然语言用例。 |
| `config/polaris_env.json` | 否 | 旧版环境配置兜底；新用户不需要优先编辑。 |
| `config/polaris_local_ports.json` | 否 | 旧版串口缓存；根配置存在时会从根配置同步。 |

## 根目录本机配置 `polaris.local.json`

首次使用：

```powershell
Copy-Item polaris.local.example.json polaris.local.json
notepad polaris.local.json
```

配置文件只放基础环境，不放运行日志、不放报告、不放大段知识库：

- `active_project`：当前要跑哪个项目，例如 `cskwb01` 或 `venusws63`。
- `common`：多个项目共用的声卡、唤醒词、默认路径和超时时间。
- `projects.cskwb01`：WB01/CSK 三端项目配置，AP + CP + WB01/ASR + 控制口。
- `projects.venusws63`：WS63/AP+WiFi 项目配置，AP + 上位/WiFi + 控制口，无 CP。

脚本读取时会先把 `common` 和 `projects.<active_project>` 合并成生效配置；所以任务和断言不用关心你当前用的是 WB01 还是 WS63，只看合并后的 `serial/audio/device/network/cloud` 字段。

默认查找顺序：命令行 `--env-file` > 任务文件 `environment.env_file` > 根目录 `polaris.local.json` > 旧版 `config/polaris_env.json`。

## config/ 目录说明

`config/` 里文件很多，但不是都要手改：

- `polaris_env.json`、`polaris_local_ports.json`：历史兼容/缓存。
- `polaris_doc_case_status.json`、`polaris_auto_executable_case_detail.md`、`polaris_fail_case_detail.md`、`polaris_failure_diagnosis.json`：运行状态、结果或诊断输出。
- `polaris_command_catalog.*`、`polaris_validation_reference.md`、`polaris_model_applicability.md`：参考资料/知识库。

当前策略是不直接删除旧文件，避免破坏历史脚本；新人和日常调试只看根目录 `polaris.local.json`。

## WB01 项目需要填写什么

WB01/CSK 项目使用 `polaris.local.json` 中的 `projects.cskwb01`，重点填写：

| 配置路径 | 说明 |
| --- | --- |
| `project_id` | 项目 ID，例如 `cskwb01`、`your_wb01_project`。 |
| `serial.topology` | 保持 `csk_ap_cp_wb01_control`，表示 AP + CP + WB01/ASR + 控制口。 |
| `serial.baudrate` | AP/CP/WB01 串口波特率，当前 WB01 映射为 `921600`。 |
| `serial.control_baudrate` | 控制口波特率，当前 WB01 映射为 `115200`。 |
| `serial.ports.ap` | AP/cskap 日志口。 |
| `serial.ports.cp` | CP/cskcp 日志口；WB01 项目通常必须填写。 |
| `serial.ports.asr` | WB01/ASR 日志口。 |
| `serial.ports.control` | 上下电/复位控制口。 |
| `serial.control_preconditions` | 声卡/PA 链路前置命令；当播放返回 0 但无唤醒证据时优先执行，例如 `uut-pa.on`、`pa-enable.set 0 17 0 1`，必须发到控制口。 |
| `audio.default_playback_device_key` | 播放唤醒词/命令词的声卡稳定 key；留空时使用电脑默认声卡。 |
| `device.wake_word` / `device.wakeup_id` | 当前唤醒词和日志里的 wakeup id。 |
| `device.iot_id` / `cloud.device_id` | 默认可留空；只有 API/云控无法通过 `deviceinfo` 自动读取时才手动填写，两者建议保持一致。 |
| `device.mac` | DUT MAC，用于确认设备身份。 |
| `network.wifi_ssid` / `network.wifi_password` | 联网、断网、在线识别、在线 VAD 场景需要。 |
| `network.enable_hotspot_control` | 只有设备连本机热点且允许脚本控制热点时才设为 `true`。 |
| `cloud.api_environment` | API 环境，按项目调试环境填 `uat` 或 `sit`。 |
| `cloud.device_env_command` | 切设备端环境的 AP/CSK 串口命令，如 `flash.set.int env@1` 或 `flash.set.int env@2`。 |
| `assertion_profile` | 保持 `cp_ap_asr_three_port`，用三端证据断言。 |

## WS63 项目需要填写什么

WS63/AP+WiFi 项目使用 `polaris.local.json` 中的 `projects.venusws63`，重点填写：

| 配置路径 | 说明 |
| --- | --- |
| `project_id` | 项目 ID，例如 `venusws63`、`your_ws63_project`。 |
| `serial.topology` | 保持 `ap_wifi_control_no_cp`，表示无 CP。 |
| `serial.baudrate` | AP/上位日志口波特率，当前 venusws63 示例为 `921600`。 |
| `serial.control_baudrate` | 控制口波特率，当前示例为 `115200`。 |
| `serial.ports.ap` | AP 日志口。 |
| `serial.ports.upper` | 上位/WiFi 日志口。 |
| `serial.ports.asr` | 兼容旧字段，通常填同一个上位/WiFi 日志口。 |
| `serial.ports.cp` | 保持空字符串 `""`，表示无 CP。 |
| `serial.ports.control` | 上下电/PA 控制口。 |
| `serial.control_preconditions` | 声卡/PA 前置命令；例如 `uut-pa.on`、`pa-enable.set 0 17 0 1`，必须发到控制口。 |
| `audio.default_playback_device_key` | 播放唤醒词/命令词的声卡稳定 key；留空时使用电脑默认声卡。 |
| `device.wake_word` / `device.wakeup_id` | 当前唤醒词和日志里的 wakeup id。 |
| `device.iot_id` / `cloud.device_id` | 默认可留空；只有 API/云控无法通过 `deviceinfo` 自动读取时才手动填写，两者建议保持一致。 |
| `device.mac` | DUT MAC，用于确认设备身份。 |
| `network.wifi_ssid` / `network.wifi_password` | 只有设备连接本机可控 Wi-Fi/热点时才填写；否则断网/联网用例应跳过或 BLOCKED。 |
| `cloud.api_environment` | API 环境，按项目调试环境填 `uat` 或 `sit`。 |
| `cloud.device_env_command` | 切设备端环境的 AP/CSK 串口命令，如 `flash.set.int env@1` 或 `flash.set.int env@2`。 |
| `assertion_profile` | 保持 `ap_upper_no_cp`，用 AP + 上位/WiFi 证据断言。 |

当前 WS63 已验证映射：AP `COM20@921600`、upper/asr `COM17@921600`、control `COM19@115200`，声卡 `VID_8765&PID_5678:9_27F546DA_3_0000`。当前 WB01 已验证映射：AP `COM13@921600`、CP `COM14@921600`、WB01/ASR `COM11@921600`、control `COM12@115200`。

### `serial`

```json
"serial": {
  "baudrate": 921600,
  "control_baudrate": 115200,
  "ports": {
    "ap": "COM13",
    "cp": "COM14",
    "asr": "COM11",
    "control": "COM12"
  }
}
```

- `baudrate`：数据日志口波特率。当前 WB01/WS63 数据口常用 `921600`。
- `control_baudrate`：控制口波特率。当前控制口常用 `115200`。
- `ports.ap`：AP/cskap 日志串口，通常用于看联网、TTS、云端、设备状态日志。
- `ports.cp`：CP/cskcp 日志串口，通常用于看离线唤醒、离线命令、关键词识别日志。
- `ports.asr`：ASR/WB01 串口，通常用于看 ASR、联网状态、WB01 侧日志。
- `ports.control`：上下电/复位控制串口；只在需要电源循环、复位、断电上电恢复时使用。

如果你机器上的串口号不同，只改这里，不要改 feature 和 mapping。

### `audio`

```json
"audio": {
  "default_playback_device_key": "",
  "playback_volume": 80,
  "capture_device_key": "",
  "loopback_device_key": "",
  "capture_device_name": "",
  "capture_sample_rate": 16000,
  "capture_channels": 1,
  "capture_duration_s": 5,
  "acoustic_oracle": {
    "thresholds": {
      "active_threshold_dbfs": -50,
      "min_rms_dbfs": -45,
      "min_peak_dbfs": -35,
      "min_active_duration_ms": 300,
      "max_clip_ratio": 0.01
    }
  }
}
```

- `default_playback_device_key`：播放设备稳定 key。建议配置，便于多声卡机器复现；如果项目/设备没有单独写声卡 key，可留空或删除，脚本会使用电脑默认播放声卡。
- `playback_volume`：建议记录目标音量，便于复现实验。脚本是否主动设置音量取决于具体 action。
- `capture_device_key` / `loopback_device_key`：回采/loopback 设备稳定 key，用于证明“真的出声”。没有配置时只能做日志级媒体 oracle。
- `capture_device_name`：当 `sounddevice` 无法直接用稳定 key 打开设备时，可填写系统录音设备名或设备索引。
- `capture_sample_rate` / `capture_channels` / `capture_duration_s`：录音采样率、通道数和录音窗口。
- `acoustic_oracle.thresholds`：声学回采阈值；默认检查 RMS、峰值、有效时长和削波比例。

新电脑首次运行时先确保 `laid` 可用：

```powershell
python tools\audio\polaris_laid.py ensure
python tools\audio\polaris_laid.py list --direction Render
python tools\audio\polaris_laid.py list --direction Capture
```

`tools/audio/polaris_laid.py` 会优先检查 `laid`，缺失时调用 `tools/audio/laid/install_laid_windows.ps1` 或 `tools/audio/laid/install_laid_linux.sh` 安装到当前用户 shell profile。查询结果中的 `Render DeviceKey` 就是 `default_playback_device_key` 推荐填写值。

如果 key 配错，常见表现是脚本 PASS 了播放命令启动，但设备听不到声音，最终唤醒/识别失败。这类应归因到播放链路或设备听音链路，不应直接判固件失败。

需要真实声学证据时可执行：

```powershell
python tools\audio\polaris_acoustic_oracle.py probe
python tools\audio\polaris_acoustic_oracle.py record --env-file polaris.local.json
python tools\audio\polaris_acoustic_oracle.py analyze --audio-file <capture.wav> --env-file polaris.local.json
```

如果录音依赖或回采设备缺失，`record` 会输出 `BLOCKED`；不能用日志级媒体 PASS 代替真实声学 PASS。

### `device`

```json
"device": {
  "wake_word": "小美小美",
  "wakeup_id": "xiao mei xiao mei",
  "model": "",
  "iot_id": "",
  "mac": ""
}
```

- `wake_word`：要播放/合成的唤醒词中文文本。更换唤醒词时改这里。
- `wakeup_id`：设备日志中的唤醒 ID，用于辅助断言和排查。
- `model`：设备型号。某些测试项只适用于特定机型时用于 skip/block 归因。
- `iot_id`、`mac`：用于确认是不是当前 DUT；默认可留空，基础唤醒/识别/命令词验证不依赖它。API/云控场景优先从 `deviceinfo` 自动读取，自动读取不到时再手动填写。

### `cloud`

```json
"cloud": {
  "api_environment": "uat",
  "device_env": "uat",
  "device_env_command": "flash.set.int env@1",
  "device_env_reboot_required": true,
  "device_id": "",
  "note": "使用 API 前，设备端 CSK/AP 环境必须和 api_environment 一致。"
}
```

- `api_environment`：API 请求使用的云端环境，通常是 `uat` 或 `sit`；必须和设备端调试环境一致。
- `device_env`：设备端当前应处于的环境，建议和 `api_environment` 保持一致。
- `device_env_command`：切换设备端 CSK/AP 环境的串口命令。
  - UAT：`flash.set.int env@1`
  - SIT：`flash.set.int env@2`
  - PRO：`flash.set.int env@0`
- `device_env_reboot_required`：切换环境后是否需要重启；当前 CSK/AP 项目默认需要 `reboot`。
- `device_id`：API 调用使用的 deviceId/IoT ID；默认可留空。脚本能通过 `deviceinfo` 读到 IoT ID 时会优先使用自动读取值；读不到且要跑 API/云控时再手动填写，建议和 `device.iot_id` 一致。

重要：只改 API 环境、不切设备端环境是不够的。执行云控/API 前，应先在 AP/CSK 串口下发 `device_env_command`，再 `reboot`，等设备联网后再调用 API。否则可能出现接口返回成功但设备不生效、connector/channel 异常或控制错环境。

强制门禁：云端配置 API 调用前，工具必须实际读取到设备端 `env` 并确认与 `cloud.api_environment` 一致。`flash.show`/`flash.get.int env` 查询失败、未解析到 env、设备仍为 PRO/env=0 但 API 配置为 UAT/SIT、或切换后未复查成功，都必须输出 `BLOCKED`，不能继续调用网络云控接口。`tools/cloud/polaris_app_control.py` 会在 `set-full-duplex`、`set-volume`、`set-mic`、`set-night-mode`、`set-wakeup-threshold`、主动播报等动作前执行该门禁。

标准顺序：

```text
查 version -> 查当前 env -> 如不一致则执行 device_env_command -> reboot -> 等待设备联网
  -> 复查 env 与 cloud.api_environment 一致 -> deviceinfo 确认 IoT ID -> 调云控 API
```

WS63/venusws63 还要额外检查云控授权版本。当前沉淀规则：`Project Version=35.03.01.01.18.26.05.04.00.02` 属于已知后台未授权 API 控制版本，应切到 `35.03.01.01.18.26.05.04.00.01` 后再进入 UAT/SIT。可执行：

```powershell
python tools\cloud\polaris_cloud_diagnostics.py --env-file <你的配置文件> --probe-cloud
```

诊断会同时检查 `version`、`flash.show`/`flash.get.int env`、`deviceinfo` 和云端业务码。HTTP 200 但业务 `code=501` 仍属于云控前置阻塞，应判 `BLOCKED`，不能判固件 FAIL。

### `network`

```json
"network": {
  "wifi_ssid": "<wifi_ssid>",
  "wifi_password": "<wifi_password>",
  "enable_hotspot_control": false
}
```

- `wifi_ssid` / `wifi_password`：联网恢复、在线识别、在线 VAD、查天气/播歌等场景需要。
- `enable_hotspot_control`：是否允许脚本接管本机热点开关。没有确认前保持 `false` 更安全。

断网/联网类场景要同时区分：热点是否真的关闭/恢复、设备是否重新在线、在线语音链路是否恢复。

断网模拟选择规则：

- DUT MAC 出现在 `hotspot-status` 客户端列表中：可使用本机热点 `hotspot-set --enable 0/1` 或 `hotspot-cycle`。
- DUT 支持写入 `vir_ssid/vir_pwd`：可写入错误 SSID/密码并重启模拟未联网，恢复时写回正确 Wi-Fi 并重启。
- DUT 连接外部 Wi-Fi/路由器：只有具备路由器/AP 电源、SSID、MAC 黑名单/ACL、防火墙或上游链路控制时才能自动断网；否则断网/联网恢复用例应 `BLOCKED/SKIPPED`。
- 不允许只因为电脑 Wi-Fi 或本机热点被关闭就判定设备断网；必须结合 `deviceinfo`、热点客户端、路由器控制证据和串口离线/上线 marker。

通用口径见 `docs/knowledge/common/network_disconnect_simulation.md`。

### `timeouts`

```json
"timeouts": {
  "observe_ms": 15000,
  "recognition_timeout_s": 15,
  "half_duplex_timeout_s": 15,
  "full_duplex_timeout_s": 60,
  "timing_guard_ms": 1200
}
```

- `observe_ms`：每次播放或动作后抓取串口证据的窗口。
- `recognition_timeout_s`：识别模式超时口径。识别模式下再次唤醒、one-shot、超时后行为都依赖这个参数。
- `half_duplex_timeout_s`：半双工识别窗口或超时口径。
- `full_duplex_timeout_s`：全双工识别窗口或超时口径。
- `timing_guard_ms`：临界时序保护时间。靠近唤醒播报、自播结束、识别超时边界时，建议预留保护窗口；落在保护窗口内的结果应标记 `TIMING_AMBIGUOUS` 或 `BLOCKED`，不要直接判固件 FAIL。

### `limits`

```json
"limits": {
  "command_limit": 20,
  "stress_rounds": 100
}
```

- `command_limit`：命令词 smoke 默认跑多少条。
- `stress_rounds`：压测默认轮次。长时间压测建议在 task 文件中单独覆盖，避免误触发。

### `paths`

```json
"paths": {
  "command_file": "docs/fa2命令词.txt",
  "debug_root": "satellite/cucumber-agent-testing/debug",
  "result_root": "result"
}
```

- `command_file`：默认命令词文件。命令词识别、打断候选、离线长播报测量可能复用。
- `debug_root`：Cucumber 调试输出根目录。不要提交 Git。
- `result_root`：底层 Polaris 工具的结果目录。不要提交 Git。

## 任务配置 `tasks/*.json`

任务配置负责回答“这次我要测什么”：

```json
{
  "schema": "polaris.cucumber.task.v1",
  "task_id": "basic_command_smoke",
  "scenario": { "tag": "basic_command_recognition" },
  "runner": { "mode": "dry-run" },
  "environment": { "env_file": "polaris.local.json" },
  "inputs": {
    "command_file": "docs/fa2命令词.txt",
    "command_limit": 20
  },
  "execution": {
    "observe_ms": 15000,
    "manage_session": true,
    "allow_side_effects": false
  }
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `task_id` | 任务唯一名称，用于报告和沟通。 |
| `scenario.tag` | 触发哪个 Cucumber 场景，必须已在 feature/mapping/registry 中沉淀。 |
| `runner.mode` | `plan-only`、`dry-run` 或 `execute`。建议示例文件默认 `dry-run`。 |
| `runner.compile_first` | 是否先用 step/action/assertion registry 离线编译 feature。新 DSL/新步骤建议设为 `true`。 |
| `environment.env_file` | 使用哪份本机环境配置。默认可不写；写了建议用 `polaris.local.json`。 |
| `inputs.command_file` | 命令词/语料文件。命令词、自由说、打断候选相关场景常用。 |
| `inputs.command_limit` | 本次最多跑多少条语料。 |
| `execution.observe_ms` | 每轮观察窗口。 |
| `execution.manage_session` | execute 时是否自动启动/停止串口 logger。新人建议 `true`。 |
| `execution.allow_side_effects` | 是否允许真机副作用。示例建议保持 `false`，执行时通过命令行显式加 `--allow-side-effects`。 |
| `execution.adapter_flows.pre/post` | 优化任务前置/收尾动作，例如 `laid_check`、`switch_device_env`、`ensure_online`、`set_full_duplex`。 |

命令行参数优先级高于任务文件，任务文件优先级高于根目录 `polaris.local.json`，最后才回退到旧版 `config/polaris_env.json`。

在线全双工可直接复制 `tasks/examples/online_full_duplex.example.json`；它会在主 Cucumber 场景前先做声卡工具检查、设备端 UAT/SIT 环境切换、在线确认和全双工 API 下发。

## 判断结果对不对

执行后优先看 `bdd_run_report.md`：

- `PASS`：前置、动作、证据和断言闭环满足当前 oracle。
- `FAIL`：证据充分且归因到固件/设备行为不符合预期。
- `BLOCKED`：前置未满足，例如串口不可用、设备未联网、声卡不存在、打断前置自播不可用。
- `TIMING_AMBIGUOUS`：动作落在超时、自播结束、唤醒播报等临界窗口，当前证据不能稳定归因。
- `REQUIREMENT_REVIEW`：需求或 oracle 不清楚，例如自由说文本容差、播报中是否允许识别没有明确口径。

原则：没有证据不要硬判固件失败；先区分环境、设备、脚本、需求，再判断固件。
