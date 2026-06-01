# 验证包 Wiki

这里保存从需求快速生成方案/用例的功能验证包。验证包不是运行日志，也不是一次性调试记录；它是后续重复执行的知识模板。

## 当前验证包

| 验证包 | 文件 | 当前入口 |
| --- | --- | --- |
| 编写规范 | `pack-schema.md` | 方案/用例生成前先读 |
| 首次唤醒 | `first-wake.md` | `first_wake`、`first_wake.example.json` |
| 识别模式唤醒 | `recognition-mode-wake.md` | `recognition_mode_wake` |
| 半双工识别 | `half-duplex.md` | `half_duplex_recognition`、`set_half_duplex` |
| 在线全双工 | `online-full-duplex.md` | `full_duplex_recognition`、全双工 task/scene |
| 基础命令词 | `basic-command.md` | `basic_command_recognition`、`basic_command.example.json` |
| 在线混合压测 | `online-mixed-stress.md` | `online_mixed_stress.example.json` |
| 误唤醒 | `false-wake.md` | `false_wake_*` tags |

新增验证包时，必须更新 `../validation-pack-index.json`，并说明是否已有 Cucumber tag、task、scene、adapter flow 和 runtime profile。
