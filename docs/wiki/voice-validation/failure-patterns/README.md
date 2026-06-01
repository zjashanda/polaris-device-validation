# 失败回归模式库

这里存放经过人工确认后的 failure-to-test-case 回归模式。生成流程：

1. 失败 run 先执行 `generate_failure_case.py` 生成 `failure_case_package.json`。
2. 人工检查 `candidate_cases.md`、`suggested_registry_updates.md`、`retest_checklist.md`。
3. 确认后执行 `register_failure_case.py --package <failure_case_package.json> --approve --approved-by <name>`。
4. 脚本会写入本目录、`tasks/generated/regression/`、`references/failure_regression_registry.json` 和 `references/scenes/generated_failure_regression.scene.example.json`。

未经过人工确认的候选不得直接进入本目录。
