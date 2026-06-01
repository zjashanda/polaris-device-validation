# Polaris 项目 Profile

这个目录用来把不同硬件项目的能力和配置分开保存，避免把一个项目的串口、断言和前置条件误用到另一个项目。

## 项目列表

| 项目 ID | 用途 | 拓扑 | 断言口径 |
| --- | --- | --- | --- |
| `cskwb01` | 保存此前已经沉淀并可执行的 CSK+WB01 项目能力 | AP + CP + WB01/ASR + 控制口 | CP/AP/ASR 三端证据 |
| `venusws63` | 记录当前调试的新 AP+WiFi/WS63 设备 | AP + 上位/WiFi + 控制口，无 CP | AP + 上位证据，无 CP |

## 使用原则

- 新增用例或 runner 先选择项目 profile，再套用该项目的串口、波特率和断言口径。
- `cskwb01` 的历史能力不要因为 `venusws63` 无 CP 而删除或降级。
- `venusws63` 的 PA 前置必须发到控制/上下电串口 `COM19@115200`。
- 当前 `venusws63` 串口映射为 AP `COM20@921600`、upper/asr `COM17@921600`、control `COM19@115200`，无 CP。
- 当前 `venusws63` 走外部 Wi-Fi（最近 IP `192.168.2.5`）；本机热点无 DUT 客户端时，断网/恢复类测试按 `BLOCKED_NETWORK_NOT_CONTROLLABLE` 留证。
- `venusws63` 云控 API 前必须先 `check-env`，确认设备端 `env=1/UAT` 与 `cloud.api_environment=uat` 一致。

## 配套文件

- `cskwb01.md`：历史 CSK+WB01 项目能力基线。
- `venusws63.md`：当前 WS63/AP+WiFi 设备调试与可测能力。
- `venusws63_run_matrix.md`：当前 WS63/AP+WiFi 设备下一步跑通验证矩阵。
- `../project_profiles.json`：机器可读的项目 profile 总表。
- `../../configs/projects/*.env.example.json`：可复制的项目环境配置模板。
