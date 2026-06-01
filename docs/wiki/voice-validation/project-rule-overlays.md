# 项目私有 Event Graph 与 Coverage 阈值沉淀

本文属于 Polaris Wiki 长期知识库，用于把新资料、异常结果和项目私有规则沉淀为后续方案/用例/断言生成依据。

## 1. 为什么要项目私有规则

WB01、WS63 或新项目的日志 marker、云端媒体字段、TTS/MP3 播放链路可能不同。通用 runtime 只负责稳定事件类型；项目差异应优先写到 overlay 和 project coverage policy，避免把私有 marker 写死到核心代码。

## 2. 当前落地点

| 类型 | 文件 | 用途 |
| --- | --- | --- |
| Event Graph overlay | `satellite/cucumber-agent-testing/references/optimization/event_graph_rules.json` | 把项目私有 marker 转成因果边或风险边。 |
| Coverage 项目阈值 | `satellite/cucumber-agent-testing/references/optimization/state_assertion_policy.json` | 在 `coverage.projects.<project_id>` 下覆盖阈值。 |
| 项目知识库 | `docs/knowledge/<project_id>/event-coverage-notes.md` | 记录 marker 来源、启用条件和复测证据。 |

## 3. 新规则启用门槛

- 至少有一份真实日志或 replay package 能证明 marker 与事件语义一致。
- 规则能解释具体用例或异常，不只是猜测。
- 已标注适用项目、适用 profile、风险等级和禁用/启用状态。
- dry-run/replay 不因规则导致误判已有 PASS 样本。

## 4. 当前项目状态

| 项目 | 当前状态 | 下一步 |
| --- | --- | --- |
| `cskwb01` | 已预留 project coverage 覆盖位和 Event Graph overlay 示例；等待更多真实在线媒体/重启/误识别日志细化。 | 有新日志后补 marker、启用规则、调整阈值。 |
| `venusws63` | 已预留无 CP 拓扑的 coverage 覆盖位；等待 AP+upper/WS63 私有媒体和 boot reason marker。 | 有新日志后补 project rule。 |

## 5. 不能直接判完成的情况

如果只有需求描述、没有真实日志，不能启用强规则；只能保留 disabled overlay、文档缺口和待复测任务。

## 6. 结构化 overlay 文件

新增 `satellite/cucumber-agent-testing/references/project_marker_overlays.json` 作为项目私有 marker 的结构化入口：

- `cskwb01`：沉淀 CP/AP/ASR 三端 wake、offline/online ASR、本地 keyword、弱执行 ACK marker。
- `venusws63`：沉淀 AP+upper wake、`online_asr_callbak`、`MSpeech Cloud 3 evt`、`DeviceControl`、`TTS url is null/no valid tts url` 等 marker。
- 该文件只定义 marker 与 coverage policy；是否升级为强断言，仍需要真实日志、回归样本或项目文档佐证。
