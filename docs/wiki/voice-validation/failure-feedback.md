# 压测/真机异常反哺 Wiki 流程

本文属于 Polaris Wiki 长期知识库，用于把新资料、异常结果和项目私有规则沉淀为后续方案/用例/断言生成依据。

## 1. 异常闭环目标

压测或真机验证出现问题时，不只输出一次性分析，还要把可复用内容沉淀为：

- 新失败模式：写入 Wiki 或项目知识库。
- 新专项用例：补到验证包 case matrix。
- 新断言规则：补 assertion-attribution、runtime profile 或 Assertion DSL。
- 新 Event Graph rule：补 `references/optimization/event_graph_rules.json` 或项目 overlay。
- 新 coverage 阈值：补 `state_assertion_policy.json` 的 `coverage.projects.<project_id>`。

## 2. 异常分类与反哺动作

| 异常类型 | 需要提取的事件 | 反哺位置 |
| --- | --- | --- |
| 重启/Crash/Watchdog | boot reason、panic/assert、重启前最后交互 | 验证包稳定性用例、Event Graph rule、coverage forbidden events |
| 媒体/TTS/MP3 异常 | cloud response、TTS URL、player start/stop/complete、HTTP error | 在线/全双工/压测验证包、media response oracle |
| 误唤醒/误识别 | 未播放目标词时的 wake/ASR/command、环境噪声、时间窗口 | 误唤醒验证包、negative case、false_wake profile |
| 超时临界不清 | AudioStarted/AudioCompleted、音频时长、session timeout marker | timing guard、TIMING_AMBIGUOUS 规则 |
| 配置/API 不生效 | API env、device env、接口返回、设备端行为 | 新项目配置文档、adapter flow、constraint rule |
| 脚本误判 | 原始日志有证据但 runner 判错 | assertion registry、runtime parser、回放样例 |

## 3. 从异常生成新用例的格式

```text
异常现象：
证据路径：
触发前置：
关键事件链：
应新增/更新的验证包：
新增用例 ID：
断言：
BLOCKED/FAIL 边界：
是否需要真实复测：
```

## 4. 执行原则

- 先用 replay/event graph/state coverage 复盘，再决定是否新增用例。
- 只有能从日志证明事件链的异常，才进入 Event Graph rule。
- 只有重复出现或需求明确的现象，才升级成强断言；单次不确定现象先做 WARN/NEEDS_REVIEW。
- 所有额外 wake/ASR/command 都要保留，不能因为主流程 PASS 而丢弃。

## 5. 自动化入口

1. 候选生成：

```powershell
python satellite\cucumber-agent-testing\scripts\generate_failure_case.py --run <run_or_optimized_dir>
```

2. 人工确认 `candidate_cases.md`、`suggested_registry_updates.md`、`retest_checklist.md`。
3. 确认后注册：

```powershell
python satellite\cucumber-agent-testing\scripts\register_failure_case.py `
  --package <failure_case_package.json> `
  --approve --approved-by <name>
```

注册脚本会更新 failure registry、生成回归 task、生成 scene 节点，并把失败模式写入 `docs/wiki/voice-validation/failure-patterns/`。未加 `--approve` 时只生成预览，不写 workspace。
