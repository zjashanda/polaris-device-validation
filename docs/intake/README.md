# 新项目/新功能资料导入入口

这里是 Polaris skill 的固定学习入口。后续有新项目说明文档、外部测试方案、类似 `voice-test-plan-designer` 的 skill、需求表格、日志样例、协议说明，都先放到这里，不要直接散放到根目录或脚本目录。

## 放置规则

每次导入一个独立资料包，按下面结构放：

```text
docs/intake/<project_id>/<YYYYMMDD_topic>/
  learning_manifest.json       # 必填，说明这批资料是什么、希望我学什么
  raw/                         # 原始资料，保持原样
    README.md
    *.md / *.txt / *.xlsx / *.pdf / *.docx
    voice-test-plan-designer/
  notes.md                     # 可选，用户补充说明
```

示例：

```text
docs/intake/venusws63/20260526_wakeup_threshold/
  learning_manifest.json
  raw/
    唤醒阈值需求说明.pdf
    测试项确认清单.xlsx
```

## 我会怎么处理

我读取 `learning_manifest.json` 后，按固定流程学习：

1. 扫描 `raw/` 资料，区分项目资料、功能资料、测试策略、命令词/自由说词表、日志 marker、配置/API/串口控制方法。
2. 输出结构化理解到 `docs/knowledge/<project_id>/`。
3. 形成缺口清单：哪些能直接自动化，哪些需要串口/API/IoT/声卡/日志 oracle，哪些只能人工复核。
4. 如果资料足够，沉淀到可执行资产：
   - `satellite/cucumber-agent-testing/features/`
   - `satellite/cucumber-agent-testing/references/`
   - `satellite/cucumber-agent-testing/tasks/examples/`
   - `satellite/cucumber-agent-testing/runtime/`
   - 必要时补充 `tools/` 最小执行工具。
5. 小样本验证后再宣布支持，不伪造未验证能力。

## learning_manifest.json 必填信息

从 `docs/intake/templates/learning_manifest.template.json` 复制一份到资料包根目录，然后填写：

- `project_id`：项目 ID，例如 `cskwb01`、`venusws63`、`new_ap_wifi_01`。
- `learning_goal`：本次希望学习新项目、新功能，还是更新已有功能。
- `material_type`：资料类型，例如 `project_docs`、`feature_docs`、`external_skill`、`test_plan_designer`、`requirements`。
- `source_files`：列出 `raw/` 下关键文件。
- `target_outputs`：希望最终沉淀成项目 profile、Cucumber 用例、Runtime 断言、压测策略、文档说明等。
- `known_device_config`：如果已知串口、声卡、Wi-Fi、云环境，可以写；真实本机执行仍以 `polaris.local.json` 为准。

## 原则

- `docs/intake/` 是原始资料入口，不直接作为执行依据。
- `docs/knowledge/` 是我学习后的稳定理解。
- `satellite/cucumber-agent-testing/` 是可执行沉淀。
- 资料不完整时，我会先输出缺口，不会强行写成 PASS 逻辑。
