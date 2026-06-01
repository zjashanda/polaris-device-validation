# Wiki 到验证包工作流

资料来源：`oldTime/legacy_20260526_144646/satellite/voice-test-plan-designer`。旧目录只作为归档来源，当前方案设计和用例生成应优先读取本 Wiki 与当前 Cucumber/Runtime registry。

## 1. 后续新项目/新功能的固定流程

```text
新需求/新项目资料
  -> 放入 docs/intake/<project_id>/<YYYYMMDD_topic>/raw/
  -> 填 learning_manifest.json
  -> 读取 docs/wiki 中已有方法
  -> 产出 docs/knowledge/<project_id>/结构化理解
  -> 生成或更新验证包：方案、用例、断言、缺口、执行入口
  -> 用户确认
  -> 进入 Cucumber/Adapter/Runtime 执行
  -> 结果反哺 Wiki 和项目知识库
```

## 2. Wiki 负责什么

- 保存功能意图、测试方法、数据设计、断言公式、归因口径。
- 给方案和用例生成提供思路，尤其是正例、反例、异常、边界、稳定性场景。
- 记录哪些测试项当前能自动化、哪些需要资料、哪些需要物理环境。

## 3. 验证包负责什么

每个功能验证包至少包含：

| 字段 | 说明 |
| --- | --- |
| `pack_id` | 功能包唯一 ID，例如 `online_full_duplex`。 |
| `requirement_scope` | 覆盖的需求意图和不覆盖范围。 |
| `preconditions` | 串口、声卡、网络、云环境、设备模式、资料前置。 |
| `case_matrix` | 正例、反例、异常、边界、稳定性用例。 |
| `assertions` | 每条用例的通过证据、禁止行为、BLOCKED 条件。 |
| `runtime_entry` | Cucumber tag、task JSON、adapter flow、runtime profile。 |
| `evidence` | 必须保存的日志、事件、replay、报告路径。 |
| `gaps` | 需要用户补充的资料、阈值、oracle 或硬件环境。 |

## 4. 输出节奏

- 执行前只集中输出：需求解读、方案、用例、断言、缺口，请用户确认。
- 执行中不反复输出零散中间产物，除非遇到安全或结论有效性阻塞。
- 执行后只输出：总报告、失败归因、证据路径、后续沉淀。

## 5. 旧资料引用规则

`oldTime/legacy_20260526_144646/satellite/voice-test-plan-designer` 是历史归档，可以作为追溯来源；但日常工作不能再依赖旧目录作为入口。若 Wiki 与 oldTime 不一致，以当前 Wiki + 当前 Cucumber/Runtime registry 为准，并在必要时把差异补回 Wiki。
