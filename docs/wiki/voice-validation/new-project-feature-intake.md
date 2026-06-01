# 新项目/新功能 Wiki 沉淀流程

本文属于 Polaris Wiki 长期知识库，用于把新资料、异常结果和项目私有规则沉淀为后续方案/用例/断言生成依据。

## 1. 固定流转路径

```text
用户资料/需求
  -> docs/intake/<project_id>/<YYYYMMDD_topic>/raw/
  -> learning_manifest.json 描述学习目标
  -> docs/wiki/voice-validation/ 补通用方法、验证包、断言归因
  -> docs/knowledge/<project_id>/ 补项目差异、配置入口、私有 marker、缺口
  -> satellite/cucumber-agent-testing/ 补 feature/registry/task/scene/runtime
  -> dry-run/precheck/smoke
  -> 真机执行和结果报告
  -> 失败模式反哺 Wiki/knowledge/runtime policy
```

## 2. 资料进入 intake 后先判断什么

| 判断项 | 处理方式 |
| --- | --- |
| 是通用测试方法 | 写入 `docs/wiki/voice-validation/` 或对应验证包。 |
| 是项目私有配置/API/串口命令 | 写入 `docs/knowledge/<project_id>/project_profile.md` 或配置说明。 |
| 是日志 marker/媒体 marker | 写入 `docs/knowledge/<project_id>/event-coverage-notes.md`，确认后进入 Event Graph rules。 |
| 是正式语料/词表/oracle | 写入项目知识库并关联 task 输入。 |
| 是临时调试结果 | 只放 debug/result，不直接进入 Wiki，除非提炼出可复用规则。 |

## 3. 学习产物最低要求

每次学习完成后至少产出：

- 可测试功能清单：哪些可自动化、哪些只能人工/专项。
- 验证包影响：新增验证包、更新已有验证包，或声明不影响通用方法。
- 执行入口影响：新增/更新 Cucumber tag、task、scene、adapter flow、runtime profile。
- 缺口清单：缺语料、缺 IoT/API、缺阈值、缺真实日志、缺声学 oracle。
- 校验结果：JSON、dry-run、precheck 或小样本 smoke。

## 4. 不能做的事

- 不把 `docs/intake/` 原始资料直接当执行依据。
- 不把项目私有规则硬编码到通用 runtime；优先用 overlay 和 policy。
- 不把资料缺失、环境阻塞或时序不明确判成固件 FAIL。
- 不把一次临时调试脚本当成已支持能力；必须进入验证包和 registry/task/runtime。
