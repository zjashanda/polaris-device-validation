# venusws63 UAT API 功能支持矩阵

- 更新时间：`2026-06-01`
- 项目：`venusws63`
- 设备：AP+WiFi，无 CP
- 串口：AP `COM20@921600`，上位/WiFi/ASR `COM17@921600`，控制口 `COM19@115200`
- 云端环境：UAT；进入方式：AP 串口执行 `flash.set.int env@1` 后 `reboot`
- deviceId/IoT ID：`210006741088068`
- 主要证据目录：
  - 主矩阵：`satellite/cucumber-agent-testing/debug/venusws63_uat_api_probe/20260520_105347_api_matrix`
  - mic/fullDuplex/upload 补验：`satellite/cucumber-agent-testing/debug/venusws63_uat_api_probe/20260520_105929_api_followup`
  - 音色补验：`satellite/cucumber-agent-testing/debug/venusws63_uat_api_probe/20260520_110606_api_character_followup_u2`
  - API 后唤醒链路确认：`satellite/cucumber-agent-testing/debug/venusws63_uat_api_probe/20260520_110934_post_api_wake_check`

## 2026-05-28 云控前置复核补充

- 当前新接线验证为 AP `COM16@921600`、上位/WiFi `COM20@921600`、控制口 `COM17@115200`。
- AP `version` 已确认 `Project Version: 35.03.01.01.18.26.05.04.00.01`，命中本地已知云控授权版本清单。
- AP `flash.show` / `flash.get.int env` 已确认 `env=1`，即 UAT，和当前 `cloud.api_environment=uat` 一致。
- UAT 云端全双工 v2 设置仍返回业务 `501` / “设备未上线，不可变更全双工状态！”；SIT 对同一 IoT ID 返回 `501` / “获取设备信息异常[未登录过的设备]！”。
- 因此当前失败不是 `.00.02` 未授权版本，也不是设备端 env 与 UAT 配置不一致；仍应归为云端在线态/目标环境注册/后台授权前置 `BLOCKED`，不能判固件 FAIL。
- 详细排查逻辑见 `docs/knowledge/venusws63/cloud-control-version-gate.md`；自动诊断工具为 `tools/cloud/polaris_cloud_diagnostics.py`。

## 2026-06-01 云控恢复补充

- 当前最新接线为 AP `COM20@921600`、upper/asr `COM17@921600`、control `COM19@115200`。
- 真实固件账号/`vir_ver` 已恢复到 `35.03.01.01.18.26.05.04.00.01`。
- `check-env` 已复核 `env=1/UAT`，与 `cloud.api_environment=uat` 一致。
- UAT 云控恢复：`set-full-duplex --enable 1`、`set-mic --enable 1`、`set-night-mode --enable 0` 最终恢复动作均 HTTP/业务码 PASS。
- 后续云控 API 的强制前置仍是 `check-env`；若 env 未解析、设备端在 PRO/env=0 或业务码异常，仍按 BLOCKED 留证。

## 结论分级

- `PASS`：HTTP/业务码成功，并且 AP/upper 抓到明确下发或执行证据；若只证明 API 成功但无设备 marker，需要降级为 PARTIAL 或作为配置前置成功。
- `PARTIAL`：HTTP/业务码成功，但本轮未抓到设备侧应用证据；后续不能直接作为 Cucumber 强断言，只能作为 API 可达性。
- `FAIL/UNSUPPORTED`：接口返回业务失败，或设备侧明确打印不支持/无法应用。

## 已支持，可沉淀为 Cucumber action/assertion

| 功能 | 接口/方法 | 本轮结论 | 关键设备证据 |
| --- | --- | --- | --- |
| 麦克风/语音开关 | `mic_switch(enable)` / `/v1/device/mic` | `PASS` | `class: mic(4), state: 2` + `mic off`；恢复时 `class: mic(4), state: 1`。 |
| 音量设置 | `set_volume(value)` / `/v1/device/volume` | `PASS` | `MSpeech Cloud 16 evt ... volume:"6"`，`refresh volume from 7 to 6`；已恢复到 `7`。 |
| 多/唯一唤醒开关 | `multi_wakeup_switch(enable)` / `/v1/device/multiWakeup/set` | `PASS` | `multi_wakeup ... enable:true`，`multi wakeup enable 1`。 |
| 唤醒词切换 | `wakeup_switch(wakeupWord)` / `/v1/wakeUpWord/set` | `PASS` | `wakeUpWord:"小美小美"`，`new wakeup word [1 小美小美]`。本轮只验证同词设置，未切到其他唤醒词。 |
| 唤醒阈值 | `wakeup_Threshold_switch(threshold)` / `/v1/device/awakeThreshold/set` | `PASS` | `MSpeech Cloud 21 evt ... threshold:"75"`，`threshold 75, source 0`。 |
| 自然对话/全双工 v1 | `fullDuplex_switch(onoroff,timeOut)` / `/v1/device/speech/fullDuplex` | `PASS` | `commonConfig.fullDuplex enable:1 timeOut:15`，`set fullduplex to on, timeout 15s`，`refresh algo timeout to 15 by fullduplex_config`。 |
| 日志设置 | `log_set(status,logLevel)` / `/v1/device/log/set` | `PASS` | `set logLevel to 3`，`MSpeech Cloud 24 evt ... status:1`；恢复关闭请求成功。 |
| 唤醒音频上传开启 v1 | `wakeupAudio_upload(onoroff)` / `/v1/device/wakeAudioUploadSwitch/set` | `PASS` | `wakeAudioUploadSwitch ... awakenAudioTypes:"1" enable:1`，`wakeup audio upload switch, enable 1`。 |
| 唤醒音频上传开启 v2 | `wakeupAudio_upload_new(onoroff)` / `/v1/device/wakeAudioUploadSwitch/set` | `PASS` | `wakeAudioUploadSwitch ... awakenAudioTypes:"1,2" enable:1`，`wakeup audio upload switch, enable 1`。 |
| 方言设置 | `accent_switch(accentId,enableAccent,mixedResEnable)` / `/v1/accent/set` | `PASS` | `class:"accent" ... accent:"mandarin" enable:"0"`，`Accent info: enable 0 ... mixed 1`。 |
| 夜间模式 | `night_mode(...)` / `/v1/smartTap/setUnDisturbConfig` | `PASS` | `cloud.instructions.nightMode ... status:0`，`nightmode off`。 |
| 主动交互播报 | `Proactive_interaction(...)` / `/v2/device/broadcast` | `PASS` | `MSpeech Cloud 5 evt ... tts ... text:"主动交互下发"`，`proc cloud broadcast`。 |
| 播控接口 | `Playback_control_interface()` / `/v1/player/control` | `PASS` | `MSpeech Cloud 19 evt ... class:"player", player:"RESUME"`，`proc cloud control`。 |

## 部分支持/需谨慎

| 功能 | 接口/方法 | 本轮结论 | 说明 |
| --- | --- | --- | --- |
| 啼哭监护 | `set_babyCare(...)` / `/v1/babyCare/set` | `PARTIAL` | 业务返回 `code=0/success`，但未抓到设备侧下发或状态变化；暂按“API 可达，设备侧未证实”处理。 |
| 自然对话/全双工 v2 | `fullDuplex_switch_new(...)` / `/v2/device/speech/fullDuplex` | `PARTIAL` | 业务返回 `code=200/success`，但关/开补验未抓到 `MSpeech Cloud/commonConfig/fullDuplex` 等明确下发；当前应优先使用 v1 接口作为 Cucumber 配置入口。 |
| 唤醒音频上传关闭 | `wakeupAudio_upload*_new(0)` | `PARTIAL` | 关闭请求返回 `code=200/success`，但本轮未抓到设备侧 `enable 0` 证据；开启路径已证实支持。若用例要断言关闭生效，需要增加“关闭后唤醒不上传”的行为断言。 |

## 暂不支持/不能直接沉淀

| 功能 | 接口/方法 | 结论 | 证据 |
| --- | --- | --- | --- |
| 发音人音色切换 | `characterValue_switch(voice_type)` / `/v2/tts/voice/set` | `FAIL/UNSUPPORTED` | `温柔女声/稳重男声/小芳/逍遥子/一菲` 返回 `501 未找到对应的音色`；`小蓝` API 返回成功，但 AP 打印 `cant find valid voiceid by xiaolan`，说明设备侧未成功应用。 |

## 收尾状态

- 已恢复麦克风开启：补验看到 `class: mic(4), state: 1`。
- 已恢复音量到 `7`：恢复接口返回成功并有 `volume:"7"` 下发证据。
- 已恢复自然对话开启/15s：v1 已有明确开启证据，v2 恢复请求仅作兼容尝试。
- 已尝试关闭日志上传和唤醒音频上传：日志关闭接口返回成功；唤醒音频上传关闭接口返回成功但设备侧关闭证据不足。
- API 后使用声卡播放“小美小美”，上位侧仍出现 `online_wakeup`，说明 mic/唤醒链路未被破坏。

## Cucumber 沉淀建议

- 可以直接沉淀：`@api_mic_switch`、`@api_set_volume`、`@api_multi_wakeup`、`@api_wakeup_word_same`、`@api_wakeup_threshold`、`@api_full_duplex_v1`、`@api_log_set`、`@api_wakeup_audio_upload_on`、`@api_accent`、`@api_night_mode`、`@api_proactive_broadcast`、`@api_player_control`。
- 暂时只做 API 可达性，不做强设备断言：`@api_babycare`、`@api_full_duplex_v2`、`@api_wakeup_audio_upload_off`。
- 暂不沉淀为可通过用例：`@api_character_voice`，除非后续提供 venusws63 支持的 voiceId/音色配置表。
