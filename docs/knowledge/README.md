# 学习沉淀区

这里存放我从 `docs/intake/` 原始资料中学习后的结构化结果。它不是原始资料区，也不是直接执行区。

推荐结构：

```text
docs/knowledge/<project_id>/
  project_profile.md            # 项目拓扑、配置、能力边界
  feature_inventory.md          # 已识别功能清单
  testable_items.md             # 可自动化测试项与断言策略
  gap_list.md                   # 缺少资料、缺少 oracle、需要用户确认的问题
  import_history.md             # 每次资料导入和学习记录
```

沉淀规则：

- 原始文件保留在 `docs/intake/<project_id>/<batch>/raw/`。
- 我学习后的稳定理解写到 `docs/knowledge/<project_id>/`。
- 只有经过小样本验证或明确可执行的内容，才进入 `satellite/cucumber-agent-testing/` 的 feature、registry、task、runtime。
- 如果只是需求理解但还不能执行，停留在 `docs/knowledge/<project_id>/gap_list.md`，不伪造自动化能力。
