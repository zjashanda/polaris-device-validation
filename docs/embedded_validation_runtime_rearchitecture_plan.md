# 嵌入式语音设备验证平台重构方案（Validation Runtime Architecture）

## 1. 文档目标

本文档用于指导当前 Polaris Cucumber + Registry + Validation Pool 测试框架，逐步演进为：

- 事件驱动（Event Driven）
- 状态驱动（State Driven）
- 场景驱动（Scene Driven）
- 可回放（Replayable）
- 可聚类（Analyzable）
- Agent 可协同（LLM Compatible）
- 大规模设备调度（Scalable Validation Platform）

的统一嵌入式设备验证平台。

本文档不是概念讨论，而是：

- 真正可落地的架构方案
- 模块职责划分
- 数据结构设计
- Runtime 执行逻辑
- 演进阶段规划
- 后续 Agent 集成方式

适用于：

- 智能语音设备
- AP/CP/ASR 多模块设备
- 在线/离线语音系统
- 半双工/全双工设备
- IoT / Embedded / AI Device 验证平台

---

# 2. 当前系统问题

当前系统虽然已经具备：

- Cucumber Feature
- Registry
- Mapping
- Validation Pool
- Failure Attribution
- Retry
- 场景分析
- 数据聚类

但仍存在核心架构问题：

| 问题 | 本质 |
|---|---|
| Runtime 仍是同步脚本 | 设备本质是异步事件系统 |
| Assertion 基于 grep | 无法稳定处理时序 |
| Cucumber 参与 runtime | Feature 与执行耦合 |
| project if/else 增长 | 无 capability 抽象 |
| replay 能力弱 | flaky 无法复现 |
| scene 只是 case sequence | 未形成状态扰动模型 |
| 日志分散 | 无统一时间轴 |
| Agent 与 Runtime 耦合 | LLM 不稳定影响执行 |

因此必须重构 Runtime 内核。

---

# 3. 最终目标架构

最终平台结构：

```text
Requirement / PRD
        ↓
LLM Planner
        ↓
Validation IR
        ↓
Scheduler / Scene Engine
        ↓
Validation Runtime
        ↓
Event Bus + Timeline + StateMachine
        ↓
Temporal Assertion Engine
        ↓
Replay + Attribution + Analytics
```

系统不再是：

```text
Feature → Script
```

而是：

```text
Feature → IR → Runtime
```

---

# 4. 核心设计原则

## 4.1 Runtime 永远 deterministic

LLM 不参与 Runtime 执行。

LLM 只负责：

- Requirement → Test Intent
- 自动生成 Feature
- 自动生成 Scene
- 自动生成语料
- 自动分析失败候选

Runtime 负责：

- 执行
- 状态管理
- 时间轴
- 事件流
- Assertion
- Replay
- 数据归档

禁止：

```text
LLM 在线决定 assertion
LLM 在线决定结果
LLM 在线执行步骤
```

---

## 4.2 Runtime 基于事件

Runtime 不允许：

```python
sleep(5)
grep(log)
```

必须：

```python
wait_event("WakeDetected")
```

系统所有行为统一抽象为事件。

---

## 4.3 Runtime 基于状态机

设备验证本质是状态迁移。

不是线性 step。

必须：

```text
IDLE → WAKE → LISTENING → ASR → TTS → MEDIA
```

所有动作必须导致状态变化。

---

## 4.4 Assertion 基于 Timeline

Assertion 不再直接读日志。

日志只是事件来源。

真正 Assertion 必须基于：

- Event
- Timeline
- State

---

# 5. 系统分层设计

# 5.1 Overall Architecture

```text
┌────────────────────────────┐
│ Requirement / Feature Layer│
└────────────┬───────────────┘
             ↓
┌────────────────────────────┐
│ Validation IR Layer        │
└────────────┬───────────────┘
             ↓
┌────────────────────────────┐
│ Scheduler / Scene Engine   │
└────────────┬───────────────┘
             ↓
┌────────────────────────────┐
│ Validation Runtime         │
└────────────┬───────────────┘
             ↓
┌────────────────────────────┐
│ Event Bus                  │
│ Timeline                   │
│ StateMachine               │
│ Temporal Assertion Engine  │
└────────────┬───────────────┘
             ↓
┌────────────────────────────┐
│ Device Adapter Layer       │
└────────────────────────────┘
```

---

# 6. Feature Layer 重构

## 6.1 Feature 不再参与 Runtime

Feature 仅负责：

- 可读性
- 测试意图
- 标签
- 需求映射

禁止在 Feature 中定义：

- timing
- retry
- assertion 细节
- runtime 行为
- 设备状态

---

## 6.2 推荐 Feature 写法

```gherkin
@wake
场景: 首次唤醒
  假如 设备空闲
  当 播放唤醒词
  那么 设备应被唤醒
```

Feature 仅表达 Intent。

Runtime 不直接执行 Feature。

---

# 7. Validation IR 设计（核心）

## 7.1 为什么需要 IR

Feature 不适合 Runtime。

因为：

- 自然语言不可组合
- 无法表达时序
- 无法表达状态
- 无法表达复杂约束

因此需要统一 IR。

---

## 7.2 IR 示例

```yaml
intent: wake_test

preconditions:
  - device_idle
  - network_online

actions:
  - inject_audio:
      file: wake.wav

expect:
  - wake_detected:
      within_ms: 3000

  - no_reboot:
      duration_ms: 10000
```

---

## 7.3 IR 字段定义

| 字段 | 含义 |
|---|---|
| intent | 测试目标 |
| preconditions | 前置状态 |
| actions | 执行动作 |
| expect | 断言 |
| timeout | 超时 |
| retry | 重试策略 |
| cleanup | 收尾动作 |
| metadata | 标签/优先级 |

---

## 7.4 所有入口统一编译 IR

统一入口：

```text
Feature → IR
Task JSON → IR
LLM Planner → IR
Replay → IR
```

Runtime 永远只认 IR。

---

# 8. Validation Runtime 设计

# 8.1 Runtime 职责

Runtime 负责：

- Action 调度
- Event 收集
- State 管理
- Timeline 管理
- Assertion 执行
- Retry
- Replay
- Evidence 归档

禁止 Runtime：

- 直接解析自然语言
- 在线依赖 LLM

---

# 8.2 Runtime 内部结构

```text
Runtime
 ├── Event Bus
 ├── Timeline Manager
 ├── StateMachine
 ├── Action Executor
 ├── Assertion Engine
 ├── Retry Controller
 ├── Replay Recorder
 └── Evidence Collector
```

---

# 9. Event Bus（最核心）

## 9.1 为什么必须 Event 化

嵌入式语音系统本质是：

- 异步
- 多模块
- 多状态
- 多时序

因此必须统一 Event。

---

## 9.2 Event 标准结构

```json
{
  "event_id": "evt_001",
  "timestamp": 123456,
  "source": "ap",
  "event_type": "WakeDetected",
  "payload": {
    "wake_word": "小美小美"
  }
}
```

---

## 9.3 Event 类型

| Event | 含义 |
|---|---|
| AudioInjected | 已播放音频 |
| WakeDetected | 检测到唤醒 |
| ASRDetected | 检测到识别 |
| TTSStarted | TTS 开始 |
| MediaStarted | 媒体播放开始 |
| MediaCompleted | 播放完成 |
| NetworkLost | 网络断开 |
| NetworkRecovered | 网络恢复 |
| RebootDetected | 设备重启 |
| TimeoutExceeded | 超时 |
| CrashDetected | 崩溃 |

---

## 9.4 日志只负责产生 Event

禁止：

```python
grep(log)
```

必须：

```text
log → event parser → event bus
```

日志不再直接参与 assertion。

---

# 10. Timeline 设计

## 10.1 Timeline 必须统一

当前：

- AP log
- CP log
- ASR log
- playback log

彼此割裂。

必须统一时间轴。

---

## 10.2 Timeline 示例

```text
00:00.000 AudioInjected
00:00.812 WakeDetected
00:01.103 ASRDetected
00:01.782 TTSStarted
00:05.001 MediaCompleted
```

---

## 10.3 Timeline 数据结构

```json
{
  "timeline": [
    {
      "ts": 0,
      "event": "AudioInjected"
    },
    {
      "ts": 812,
      "event": "WakeDetected"
    }
  ]
}
```

---

# 11. StateMachine 设计

## 11.1 Runtime 必须显式状态化

设备行为本质是状态迁移。

必须维护 Runtime 状态。

---

## 11.2 推荐状态

```text
IDLE
WAKE_PENDING
LISTENING
ASR_PROCESSING
TTS_PLAYING
MEDIA_PLAYING
NETWORK_LOST
REBOOTING
```

---

## 11.3 状态迁移示例

```text
IDLE
  ↓ WakeDetected
LISTENING
  ↓ ASRDetected
ASR_PROCESSING
  ↓ TTSStarted
TTS_PLAYING
```

---

# 12. Temporal Assertion Engine

# 12.1 为什么必须 Temporal Assertion

传统 assertion：

```text
有没有 wake
```

不够。

真正问题是：

```text
什么时候 wake
wake 后多久 asr
asr 后是否 reboot
```

因此必须支持时间逻辑。

---

# 12.2 Assertion 类型

## 顺序断言

```text
Wake 后必须存在 ASR
```

---

## 时间窗口断言

```text
Wake 必须发生在 3 秒内
```

---

## 排斥断言

```text
ASR 后 10 秒内不允许 reboot
```

---

## 状态断言

```text
media 播放期间不允许进入 reboot
```

---

# 12.3 Assertion 示例

```yaml
expect:
  - event:
      type: WakeDetected
      within_ms: 3000

  - no_event:
      type: RebootDetected
      duration_ms: 10000
```

---

# 13. Capability Runtime

## 13.1 为什么不能继续按 project 写逻辑

当前：

```python
if venusws63:
```

长期一定崩溃。

必须 capability 化。

---

## 13.2 Capability 示例

```yaml
capabilities:
  cp_log: false
  online_asr: true
  full_duplex: true
  low_power: false
```

---

## 13.3 Runtime 自动降级

例如：

```text
没有 cp_log
→ 自动关闭 CP assertion
```

不是：

```python
if ws63
```

---

# 14. Device Adapter Layer

## 14.1 Adapter 职责

统一设备接入。

Runtime 不关心具体设备。

---

## 14.2 Adapter 类型

| Adapter | 职责 |
|---|---|
| Serial Adapter | 串口 |
| Audio Adapter | 播放 |
| Cloud Adapter | 云控 |
| Network Adapter | 网络 |
| Power Adapter | 上下电 |
| Log Adapter | 日志采集 |

---

## 14.3 Adapter 输出统一 Event

例如：

```text
serial log → WakeDetected
cloud callback → TTSStarted
```

---

# 15. Scene Engine（重点）

# 15.1 为什么必须 Scene 化

真实问题大量来自：

- 状态污染
- 顺序依赖
- 长时间运行
- 网络切换
- 多功能组合

因此必须 Scene First。

---

# 15.2 Scene 不再是 case list

错误：

```text
CaseA → CaseB
```

正确：

```text
State Transition Graph
```

---

# 15.3 Scene 示例

```text
WAKE
 → MEDIA
 → NETWORK_LOST
 → NETWORK_RECOVER
 → ASR
 → REBOOT
```

---

# 15.4 Scene Generator

必须支持：

| 类型 | 描述 |
|---|---|
| Random | 随机场景 |
| Weighted | 权重场景 |
| Failure Driven | 基于历史失败 |
| State Sensitive | 基于状态污染 |
| Long Running | 长时间压测 |

---

# 15.5 Scene 数据结构

```json
{
  "scene_id": "scene_001",
  "events": [
    "wake",
    "media",
    "network_disconnect"
  ]
}
```

---

# 16. Replay System

# 16.1 为什么 Replay 必须存在

后期 80% 问题是 flaky。

没有 replay 无法定位。

---

# 16.2 Replay Package

```text
run/
 ├── timeline.json
 ├── events.json
 ├── states.json
 ├── ap.log
 ├── cp.log
 ├── asr.log
 ├── playback.wav
 ├── runtime_state.json
 └── metadata.json
```

---

# 16.3 Replay 流程

```text
Load Timeline
→ Replay Event
→ Replay State
→ Re-run Assertion
```

---

# 17. 数据分析层

# 17.1 数据必须资产化

不能只是日志。

必须形成：

- Event Asset
- Scene Asset
- Failure Asset
- Device Health Asset

---

# 17.2 Failure Fingerprint

不要直接基于文本。

应该：

```yaml
fingerprint:
  - WakeDetected
  - ASRDetected
  - HTTPTimeout
  - MediaMissing
```

---

# 17.3 Failure 分类

| 类型 | 说明 |
|---|---|
| STABLE_FAIL | 稳定失败 |
| FLAKY_FAIL | 偶现 |
| ENV_RELATED | 环境相关 |
| TIMING_ISSUE | 时序问题 |
| REQUIREMENT_GAP | 需求不明确 |

---

# 18. Device Health System

## 18.1 设备健康度必须长期统计

设备问题会污染测试。

必须建立设备健康度系统。

---

## 18.2 健康指标

| 指标 | 含义 |
|---|---|
| reboot_count | 重启次数 |
| cpu_avg | CPU |
| mem_avg | 内存 |
| flash_erase | Flash 擦写 |
| log_disconnect | 日志中断 |
| online_rate | 在线率 |

---

## 18.3 健康度用途

- 自动避开坏设备
- 自动降权设备
- 自动设备隔离

---

# 19. Agent 集成方案

# 19.1 Agent 永远不直接执行 Runtime

禁止：

```text
LLM 决定 PASS/FAIL
```

---

# 19.2 Agent 正确职责

LLM 负责：

| 能力 | 描述 |
|---|---|
| Requirement Parse | 解析需求 |
| Feature Generate | 生成 feature |
| IR Generate | 生成 IR |
| Edge Case Generate | 边界生成 |
| Scene Suggestion | 场景建议 |
| Failure Summary | 失败总结 |
| Root Cause Candidate | 根因候选 |

---

# 19.3 Runtime 永远 deterministic

Runtime：

- 不联网
- 不依赖 LLM
- 不动态生成 assertion
- 不动态生成动作

---

# 20. 本地任务入口与调度系统

当前 skill 不落地外部调度集成；Runtime 只暴露稳定 CLI/task 入口。
如后续需要外部调度，也只能调用 CLI 并归档产物，不能参与业务判断。

## 20.1 Scheduler 职责

Scheduler 负责：

- 设备分配
- Scene 调度
- 资源锁
- Retry 调度
- 长时间运行

---

# 21. 推荐目录结构

```text
validation-platform/
 ├── features/
 ├── ir/
 ├── runtime/
 │    ├── event_bus/
 │    ├── timeline/
 │    ├── state_machine/
 │    ├── assertion_engine/
 │    └── replay/
 ├── adapters/
 ├── scene_engine/
 ├── analytics/
 ├── scheduler/
 ├── datasets/
 ├── replay_runs/
 └── llm_tools/
```

---

# 22. 推荐演进阶段（非常关键）

# Phase 1（最优先）

目标：

统一 Event Layer。

必须完成：

- log → event parser
- event bus
- timeline

禁止继续堆 feature。

---

# Phase 2

目标：

Assertion Engine 重构。

必须完成：

- temporal assertion
- event assertion
- state assertion

逐步淘汰 grep。

---

# Phase 3

目标：

Scene Engine。

必须完成：

- scene graph
- random scene
- failure driven scene
- state pollution detect

---

# Phase 4

目标：

Replay System。

必须完成：

- replay package
- replay execution
- replay assertion

---

# Phase 5

目标：

IR Compiler。

必须完成：

```text
feature → IR
task → IR
agent → IR
```

---

# Phase 6

目标：

Agent Integration。

必须完成：

- requirement parser
- auto scene generation
- failure summarize
- edge case generation

禁止 Agent 接管 Runtime。

---

# 23. 最终平台能力

最终平台应该具备：

| 能力 | 说明 |
|---|---|
| Event Driven Runtime | 事件驱动 |
| Temporal Assertion | 时序断言 |
| Replay | 可回放 |
| Scene Engine | 场景驱动 |
| Failure Clustering | 失败聚类 |
| Device Health | 设备健康 |
| Capability Runtime | 能力适配 |
| Agent Compatible | Agent 协同 |
| Large Scale Validation | 大规模调度 |

---

# 24. 最终结论

当前 Polaris 框架已经具备：

- BDD
- Registry
- Contract
- Validation Pool
- Attribution
- Retry
- Scene 概念
- Failure 分析

说明测试“语义层”已经成熟。

当前真正缺失的是：

# Runtime 内核。

必须完成：

- Event Runtime
- Timeline
- StateMachine
- Temporal Assertion
- Replay
- Scene Engine

之后系统会从：

```text
自动化测试框架
```

演进成：

# Embedded Validation Platform

甚至进一步演进为：

# Embedded Validation Operating System

