# FA2 命令蜂鸣器期望表

本文件由 `build_fa2_beep_expectation_table.py` 生成，是规则期望表，不是物理蜂鸣器实测事实表。
录音/声学回采未落地时，缺少蜂鸣器证据只能归为 `UNKNOWN` 或 `evidence_gap`，不能伪造成 PASS。

- 命令总数：`343`
- 来源：`D:\revolution4s\Polaris\docs\fa2命令词.txt`
- 蜂鸣器期望分布：`{"expected_if_state_changes": 263, "not_required": 80}`
- 命令类型分布：`{"power_control": 2, "mode_control": 6, "temperature_control": 38, "airflow_control": 102, "feature_toggle": 83, "volume_control": 13, "timer_control": 19, "online_or_general": 75, "query": 4, "network_query_or_setup": 1}`

## 断言口径

- `expected_if_state_changes`：控制类命令只有发生状态变化时才期望执行/蜂鸣反馈。
- `not_expected_if_noop`：重复打开/重复关闭、已是当前模式等 no-op 场景不强制蜂鸣器。
- `not_required`：查询、联网状态、媒体/问答等不要求执行机构或蜂鸣器。
- `unknown_need_project_rule`：需要项目私有规则或更多日志 marker 后再提升为强断言。

## 产物

- JSON：`D:\revolution4s\Polaris\docs\wiki\voice-validation\fa2-command-beep-expectation.json`
- CSV：`D:\revolution4s\Polaris\docs\wiki\voice-validation\fa2-command-beep-expectation.csv`
