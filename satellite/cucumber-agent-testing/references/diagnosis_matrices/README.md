# 控制变量诊断矩阵

本目录存放可复用的故障定位矩阵。矩阵不是最终回归用例，作用是把一个失败现象拆成多个单变量对照项，帮助判断失败属于音频、唤醒、模式、时序、命令域、云端、媒体链路、日志 oracle 还是需求口径。

执行入口：

```powershell
$env:PYTHONIOENCODING='utf-8'
python satellite\cucumber-agent-testing\scripts\run_command_control_diagnosis.py `
  --env-file satellite\cucumber-agent-testing\debug\local_envs\venusws63.polaris.local.json `
  --matrix-file satellite\cucumber-agent-testing\references\diagnosis_matrices\ws63_open_ac_control_variable.example.json `
  --allow-side-effects
```

输出目录默认在 `satellite/cucumber-agent-testing/debug/command_control_diagnosis/<timestamp>/`，其中 `report.md`、`summary.json`、`matrix.csv`、`session/logs/live/merged.log` 是核心证据。

## 已沉淀矩阵

| 矩阵 | 用途 |
| --- | --- |
| `ws63_open_ac_control_variable.example.json` | WS63 打开空调/全双工/时序问题定位矩阵。 |
| `fa2_control_beep_probe.example.json` | FA2 控制命令与蜂鸣器/执行反馈小规模探针，覆盖开关机 transition/no-op、模式、查询、音量、联网状态、新闻对照。 |

全量 343 条命令 baseline 可由 `docs/fa2命令词.txt` 生成；运行结果不提交到仓库，只保存在 `debug/goal_command_beep/<timestamp>/`。

```powershell
python satellite\cucumber-agent-testing\scripts\build_fa2_command_matrix.py `
  --output satellite\cucumber-agent-testing\debug\goal_command_beep\<timestamp>\matrices\fa2_all_commands_baseline.json
```

## 2026-05-29 补充：串口覆盖与补验证矩阵

- `run_command_control_diagnosis.py` 现在会在 run 根目录输出 `serial_coverage.json`，并把 `serial_coverage` 写入 `summary.json`、`matrix.csv` 和单 case `result.json`。
- 若所有日志串口都打不开，或通过 `--required-serial-roles` 指定的必需口打不开，结果为 `BLOCKED`。
- 若至少一个日志口可用但部分配置口打不开，结果继续执行，但标记 `COVERAGE_DEGRADED`；当前 WS63 示例为 AP/COM20 正常而 upper/COM17 被占，只能给 AP 侧降级结论。
- WS63 COM20 小规模补验证矩阵：`venusws63_com20_supplement.example.json`，覆盖首次唤醒、空调开关/模式/查询、音量、新闻/音乐，不重跑 343 条。

严格要求 AP+upper 均打开时可加：

```powershell
python satellite\cucumber-agent-testing\scripts\run_command_control_diagnosis.py `
  --allow-side-effects `
  --env-file satellite\cucumber-agent-testing\debug\goal_command_beep\20260528_201015\envs\venusws63.env.json `
  --matrix-file satellite\cucumber-agent-testing\references\diagnosis_matrices\venusws63_com20_supplement.example.json `
  --required-serial-roles ap,upper
```
