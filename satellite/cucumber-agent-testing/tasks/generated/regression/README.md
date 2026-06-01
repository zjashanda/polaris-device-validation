# 生成回归任务目录

本目录用于存放经人工确认后的 failure-to-test-case 回归 task。

固定流程：

1. `generate_failure_case.py --run <failed_run>` 只生成候选，不直接修改执行资产。
2. 人工确认候选合理后，执行 `register_failure_case.py --package <failure_case_package.json> --approve --approved-by <name>`。
3. 生成的 task 默认 `runner.mode=dry-run`、`allow_side_effects=false`，真机执行前必须显式确认副作用。

不要手工把未经确认的临时 debug 产物复制到这里。
