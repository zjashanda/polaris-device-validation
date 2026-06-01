# Polaris 需求实现核对与双真机验证记录

> 日期：2026-05-27  
> 目标：核对 `docs/skill/polaris-system-requirements.md` 与当前实现，补齐可立即闭环的缺口，并用已连接的 WB01/WS63 真机验证。
> 2026-06-01 更新：本文早期表格保留历史 run 的串口证据，不代表当前接线。当前 WS63 为 AP `COM20@921600`、upper/asr `COM17@921600`、control `COM19@115200`；当前 WB01 为 AP `COM13@921600`、CP `COM14@921600`、WB01/ASR `COM11@921600`、control `COM12@115200`。

## 1. 本轮结论

- 系统化需求文档已生成：`docs/skill/polaris-system-requirements.md`。
- 已补齐 P0 缺口：需求到方案/用例的固定生成入口。
- 已补齐 P0 缺口：多次执行结果的总报告汇总入口。
- 已补齐真实问题闭环：execute 前增加串口打开探测，端口被 Xshell/串口助手/旧 logger 占用时先判 `BLOCKED`，避免误判固件 `FAIL`。
- 已补齐云控项目化缺口：cloud adapter 现在会把当前 `--env-file` 传给 `polaris_app_control.py`，避免 WS63/WB01 切换时仍读取根目录或旧配置导致串口/API 环境错用。
- WB01 与 WS63 都完成真机 Cucumber/Runtime smoke：首次唤醒 + 基础命令词 3 条小样本，4 个 execute run 均 `PASS`。
- 在线全双工已完成 WB01/WS63 dry-run 对齐；全量 FD-002~FD-012 真机 execute 仍需确认副作用后单独执行。

## 2. 需求核对结果

| 需求域 | 需求目标 | 当前实现 | 本轮处理 | 状态 |
| --- | --- | --- | --- | --- |
| 需求到方案/用例 | 用户给一句需求后快速生成方案、用例、缺口、确认单 | 之前主要依赖 Wiki + Agent 整理 | 新增 `generate_requirement_package.py` | 已补齐 |
| 确认到执行 | 已支持功能走 Cucumber/Task/Adapter/Runtime | `run_task.py`、`run_optimized_task.py`、`run_kernel_scene.py` 已存在 | 用真机跑 `run_optimized_task.py` 验证 | 已验证 |
| 结果总报告 | 多 run 汇总成用户可读总报告 | 单 run 已有 BDD/Runtime 报告 | 新增 `build_validation_summary_report.py` | 已补齐 |
| 串口占用归因 | 端口不可打开应判 BLOCKED | 旧流程在 WS63 AP 被占用时会进入 FAIL | preflight 增加串口打开探测，BDD 汇总读取 logger open error | 已补齐 |
| 云控项目化 | API 操作必须使用当前任务/env-file 的项目配置 | 旧云控辅助脚本存在 COM14/COM13 和旧配置倾向 | cloud adapter 和 `polaris_app_control.py` 改为传入并读取当前 env-file | 已补齐 |
| WB01 项目 | AP/CP/ASR/control 四串口，声卡播放，真机执行 | 已配置 `cskwb01` | 首唤醒、基础命令词 smoke PASS | 已验证 |
| WS63 项目 | AP/upper/control，无 CP，声卡播放，真机执行 | 已配置 `venusws63` | 释放 Xshell 占用 COM16 后，首唤醒、基础命令词 smoke PASS | 已验证 |
| 声卡工具 | 新电脑能检查/安装 laid | `tools/audio/polaris_laid.py` 已存在 | 本轮验证 laid 已安装且列出目标声卡 | 已验证 |
| PA 恢复 | 播放不生效时控制口发 PA 命令 | `pa_recover` flow 已存在 | WB01/WS63 均执行 PASS | 已验证 |
| 在线全双工 | FD-001~FD-012 方案与入口 | 验证包、task、scene 已存在 | 本轮 dry-run PASS | 部分完成 |
| 媒体真实出声 oracle | 不只看云端返回，要证明设备真的播 | 当前主要依赖设备日志 marker | 未配置 loopback/capture | 外部条件缺口 |
| 项目私有规则 | 基于真实日志细化 Event Graph/coverage | 已有 overlay 位置 | 新增 WS63 COM16 占用知识 | 持续项 |

## 3. 本轮新增/修改

| 文件 | 说明 |
| --- | --- |
| `docs/skill/polaris-system-requirements.md` | 系统化需求文档，含流程图、架构、实现细节和偏差点。 |
| `satellite/cucumber-agent-testing/scripts/generate_requirement_package.py` | 从 Wiki/验证包生成方案、用例矩阵、缺口和确认单。 |
| `satellite/cucumber-agent-testing/scripts/build_validation_summary_report.py` | 汇总多个 run/optimized_run，生成总报告。 |
| `satellite/cucumber-agent-testing/runtime/constraint_engine.py` | execute preflight 增加串口打开探测。 |
| `satellite/cucumber-agent-testing/scripts/run_cucumber.py` | BDD 汇总读取 managed logger 端口错误，避免缺日志误判固件 FAIL。 |
| `tools/cloud/polaris_app_control.py` | 支持 `--env-file`，按当前项目 AP/ASR 口采集 deviceinfo 和日志窗口。 |
| `satellite/cucumber-agent-testing/runtime/device_adapter.py` | cloud adapter 命令模板传入 `--env-file {env_file}`。 |
| `satellite/cucumber-agent-testing/scripts/plan_adapter_flow.py` | adapter flow 默认上下文增加 `env_file` 并传给 action。 |
| `satellite/cucumber-agent-testing/scripts/run_adapter_action.py` | 单 action dry-run/execute 自动传入当前 env-file。 |
| `README.md` | 增加需求包生成、总报告汇总、串口占用预检说明。 |
| `SKILL.md` | 增加新入口和串口占用归因规则。 |
| `docs/skill/requirement-to-execution-workflow.md` | 增加固定需求包生成入口、总报告入口和端口占用 BLOCKED 规则。 |
| `docs/knowledge/venusws63/event-coverage-notes.md` | 记录 WS63 COM16 被 Xshell 占用导致 AP 证据缺失的问题和处理策略。 |

## 4. 真机验证证据

| 项目 | 用例 | 配置 | 结果 | 关键指标 | 证据目录 |
| --- | --- | --- | --- | --- | --- |
| `cskwb01` | 首次唤醒 | AP=COM14, CP=COM13, ASR=COM12, control=COM15, 声卡 `VID_8765&PID_5678:9_2A847557_7_0000` | PASS | CP=1, AP=1, ASR=3, runtime events=26 | `satellite/cucumber-agent-testing/debug/runs/20260527_174844_088_execute` |
| `venusws63` | 首次唤醒 | AP=COM16, upper/asr=COM20, control=COM17, 声卡 `VID_8765&PID_5678:9_27F546DA_3_0000` | PASS | AP=1, ASR=2, runtime events=18 | `satellite/cucumber-agent-testing/debug/runs/20260527_180023_403_execute` |
| `cskwb01` | 基础命令词 3 条 | 同上 | PASS | total=3, PASS=3, runtime events=161 | `satellite/cucumber-agent-testing/debug/runs/20260527_181228_611_execute` |
| `venusws63` | 基础命令词 3 条 | 同上 | PASS | total=3, PASS=3, runtime events=74 | `satellite/cucumber-agent-testing/debug/runs/20260527_181325_325_execute` |

汇总报告：`satellite/cucumber-agent-testing/debug/reports/20260527_181429/validation_summary_report.md`，4 个真机 execute run 全部 `PASS`。

## 5. 本轮发现并闭环的问题

### 5.1 WS63 AP 串口被 Xshell 占用

- 现象：WS63 首次唤醒第一次执行时，ASR/upper 侧已出现 `online_wakeup`，但 AP=COM16 无日志，原结果被判 `FAIL`。
- 根因：`COM16` 被 Xshell 窗口 `016-COM016 - Xshell 7` 占用，managed logger 无法打开 AP 口。
- 处理：停止 Xshell 相关进程后，COM16 可打开；重新执行 WS63 首唤醒 `PASS`。
- 代码闭环：execute preflight 增加串口打开探测；run_cucumber 汇总读取 managed logger open error，端口占用时判 `BLOCKED`。

## 6. 静态校验

- JSON 校验：排除 `debug/oldTime/__pycache__` 后检查 40 个 JSON，全部可解析。
- Python 编译：`satellite/cucumber-agent-testing/scripts`、`runtime`、`tools` compileall 通过。
- 需求包生成 smoke：`generate_requirement_package.py --requirement "在线全双工相关功能验证"` PASS，命中 `full_duplex_recognition` 验证包。
- 在线全双工 dry-run：WB01/WS63 的 `online_full_duplex.example.json` dry-run 均 PASS。
- 云控 adapter dry-run：WS63 `set_full_duplex` 渲染命令已携带 `--env-file satellite/.../venusws63.polaris.local.json`，不再硬编码 WB01 端口。

## 7. 当前仍未完全落地

| 未完成项 | 原因 | 下一步 |
| --- | --- | --- |
| 在线全双工 FD-002~FD-012 全量真机 execute | 会切设备环境/全双工，触发在线媒体和较多副作用 | 用户确认范围后按 scene 全量跑。 |
| 媒体/TTS/MP3 真实声学 oracle | 当前未配置 loopback/capture 声卡 | 配置 `capture_device_key` 或 `loopback_device_key`。 |
| WB01/WS63 项目私有 Event Graph 强规则 | 需要更多真实压测/异常日志 | 压测后把真实 marker 写入 overlay。 |
| 自动 failure-to-test-case pipeline | 已有方法和报告基础，但还未一键生成专项草案 | 后续新增脚本从失败 run 生成候选 case/rule。 |
| L2/L3 声学专项 | 需要正式语料、阈值、噪声场、DOA/人群/距离 rig | 等资料和硬件条件齐备后接入。 |

## 8. 使用建议

- 给一句需求时，先跑 `generate_requirement_package.py` 生成方案/用例/缺口/确认单。
- 用户确认副作用后，用 `run_optimized_task.py` 或 `run_kernel_scene.py` 真机执行。
- 执行后用 `build_validation_summary_report.py` 汇总多个 run。
- 如果 execute preflight 报串口占用，先关闭 Xshell、串口助手、旧 logger，再重跑；这类问题属于 `BLOCKED`，不是固件 FAIL。
