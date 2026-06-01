# Polaris Wiki 知识库

本目录是当前新逻辑的长期知识库入口，用来支撑“需求 -> 方案/用例 -> 确认 -> 执行 -> 报告”的工作流。

## 使用顺序

1. 先读 `voice-validation/README.md`，确认语音验证知识库范围。
2. 再按需求读取对应专题：唤醒、命令词、自由说、在线识别、误唤醒。
3. 生成方案和用例时同时读取 `voice-validation/test-item-index.md`、`voice-validation/assertion-attribution.md` 和对应验证包。
4. 新项目/新功能资料进入 `docs/intake/` 后，学习成果要反哺到本 Wiki，不能只留在临时日志或对话里。

## 与其他目录的关系

- `docs/wiki/`：长期方法、测试思路、断言归因和验证包 Wiki。
- `docs/knowledge/<project_id>/`：某个项目学习后的结构化知识和差异。
- `satellite/cucumber-agent-testing/`：可执行 Cucumber/Adapter/Runtime。
- `oldTime/`：历史归档，只作为追溯来源，不作为当前执行入口。
