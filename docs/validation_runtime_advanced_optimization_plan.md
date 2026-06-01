# Embedded Validation Runtime 高级优化与演进方案

## 文档目标

本文档用于在现有《Embedded Validation Runtime Architecture》基础上，进一步补足：

- Runtime Plugin 化
- Event Graph
- Constraint Scene Engine
- Replay VM
- Resource Runtime
- Device Simulation
- Assertion DSL
- Event Versioning
- Failure Exploration
- Distributed Runtime
- Validation Kernel

目标是避免系统后期演化为：

```text
God Runtime
```

最终形成：

# Enterprise Grade Embedded Validation Runtime

甚至进一步演进为：

# Embedded Validation Operating System

---

# 1. 当前架构最大风险

当前方案方向正确：

- Event Driven
- Timeline
- StateMachine
- Replay
- Scene Engine
- Agent Compatible

但存在一个非常危险的问题：

# Runtime 仍可能中心化膨胀。

随着：

- wake
- asr
- tts
- media
- bluetooth
- ota
- low power
- network
- cloud
- multiroom
- skill

等模块持续接入，Runtime 会逐渐演变为：

```text
超级 Runtime
```

最终：

- 逻辑耦合
- assertion 污染
- replay 不稳定
- flaky 无法归因
- plugin 互相影响
- scene 爆炸

因此必须进一步内核化。

---

# 2. Validation Kernel Architecture

后续推荐架构：

```text
                 ┌──────────────────┐
                 │ Validation DSL   │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ IR Compiler      │
                 └────────┬─────────┘
                          ↓
               ┌──────────────────────┐
               │ Validation Kernel    │
               └────────┬─────────────┘
                        ↓
      ┌──────────────────────────────────┐
      │ Runtime Plugin System            │
      ├──────────────────────────────────┤
      │ wake plugin                      │
      │ media plugin                     │
      │ network plugin                   │
      │ tts plugin                       │
      │ reboot plugin                    │
      │ ota plugin                       │
      └──────────────────────────────────┘
                        ↓
           ┌────────────────────────┐
           │ Device Adapter Layer   │
           └────────────────────────┘
```

---

# 3. Runtime Plugin 化（最高优先级）

# 3.1 为什么必须 Plugin 化

当前 Runtime：

```text
Runtime
 ├── Event Bus
 ├── Timeline
 ├── Assertion
 ├── Replay
 └── StateMachine
```

随着业务增长：

所有模块都会往 Runtime 核心塞逻辑。

后期会变成：

```text
50000+ lines Runtime
```

必须拆解。

---

# 3.2 推荐 Plugin Architecture

```text
runtime/
 ├── kernel/
 ├── plugins/
 │    ├── wake/
 │    ├── asr/
 │    ├── media/
 │    ├── network/
 │    ├── reboot/
 │    ├── ota/
 │    ├── bluetooth/
 │    └── low_power/
```

---

# 3.3 Plugin 职责

每个 Plugin 必须独立负责：

| 能力 | 示例 |
|---|---|
| Event Parser | WakeDetected |
| Assertion | wake assertion |
| State | wake state |
| Replay | wake replay |
| Analytics | wake metrics |
| Scene Mutation | wake perturbation |

---

# 3.4 Plugin 生命周期

推荐：

```python
class RuntimePlugin:

    def on_init(self):
        pass

    def on_event(self, event):
        pass

    def on_assertion(self, assertion):
        pass

    def on_replay(self):
        pass

    def on_shutdown(self):
        pass
```

---

# 4. Event Schema 正式化

# 4.1 Event 是系统核心

后期：

- Replay
- Analytics
- Attribution
- Scene
- Failure Cluster
- Agent

全部依赖 Event。

因此 Event 必须正式 schema 化。

---

# 4.2 当前 Event 风险

当前：

```json
{
  "event_type": "WakeDetected"
}
```

太弱。

无法支持：

- versioning
- replay compatibility
- distributed runtime
- multi-device
- cross timeline

---

# 4.3 推荐 Event Schema

```json
{
  "event_id": "evt_xxx",
  "event_type": "WakeDetected",
  "event_version": "v2",
  "run_id": "run_001",
  "scene_id": "scene_001",
  "device_id": "ws63_001",
  "plugin": "wake",
  "source": "asr",
  "timestamp_monotonic": 12345,
  "timestamp_wall": 1710000000,
  "severity": "info",
  "tags": ["wake", "asr"],
  "payload": {}
}
```

---

# 4.4 Monotonic Timestamp（极其关键）

Timeline：

# 必须使用 monotonic timestamp。

不能依赖 wall clock。

否则：

- NTP 校时
- 系统时间变化
- reboot
- 时区

会毁掉 replay。

---

# 5. Timeline → Event Graph

# 5.1 Timeline 不应该只是 list

当前：

```json
timeline: []
```

后期不够。

因为设备是：

# 多模块并行系统。

例如：

- AP
- CP
- ASR
- Cloud
- Media
- Bluetooth

同时工作。

---

# 5.2 推荐 Event Graph

真正结构：

```text
AudioInjected
      ↓
WakeDetected
      ↓
ASRDetected
      ↓
CloudRequest
      ↓
TTSStarted
```

本质：

# Directed Acyclic Graph（DAG）

---

# 5.3 Event Relation

Event 必须支持：

```json
{
  "event_id": "evt_002",
  "parent_event": "evt_001",
  "caused_by": "evt_001"
}
```

---

# 5.4 Event Graph 价值

后续：

- Root Cause
- Replay
- Attribution
- Failure Analysis
- Latency Analysis

都会极大增强。

---

# 6. Constraint Scene Engine

# 6.1 当前 Scene 风险

当前：

```text
Random Scene
```

后期：

# 组合爆炸。

---

# 6.2 Constraint System（必须）

必须引入：

# Constraint Engine

例如：

```yaml
constraints:
  - wake_requires_idle
  - media_before_tts_forbidden
  - reboot_not_during_ota
  - network_required_for_cloud_asr
```

---

# 6.3 Scene Generator 必须 Constraint Aware

否则：

Agent 会生成：

- 无效 scene
- 不可执行 scene
- 无意义 scene

导致：

- replay 污染
- analytics 污染
- flaky 假阳性

---

# 6.4 Scene Mutation Engine

后续必须支持：

```text
scene
 ↓
mutation
 ↓
new scene
```

例如：

- timing mutation
- network perturbation
- reboot insertion
- media interruption

---

# 7. Hierarchical StateMachine

# 7.1 当前状态机不够

当前：

```text
MEDIA_PLAYING
```

后期一定爆炸。

---

# 7.2 必须支持层级状态机

例如：

```text
MEDIA
 ├── MUSIC
 ├── TTS
 ├── ALARM
 └── RADIO
```

---

# 7.3 Parallel StateMachine

后期甚至需要：

```text
AudioState
NetworkState
CloudState
PowerState
```

并行状态机。

---

# 8. Resource Runtime（后期核心）

# 8.1 为什么必须资源模型

很多 bug：

本质是：

# Resource Conflict

例如：

- mic 被占用
- speaker 被占用
- focus 未释放
- network channel 被阻塞

---

# 8.2 推荐 Resource Model

```yaml
resources:
  mic:
    owner: wake_engine

  speaker:
    owner: media

  network:
    owner: cloud_asr
```

---

# 8.3 Resource Runtime 能力

必须支持：

- lock
- release
- contention detect
- deadlock detect
- starvation detect

---

# 9. Replay VM（重要）

# 9.1 当前 Replay 不够

当前：

```text
Replay Event
```

不够。

---

# 9.2 真正需要 Replay VM

Replay 应该像：

# 虚拟机。

支持：

- snapshot
- time travel
- rollback
- state injection
- fault injection

---

# 9.3 Replay Snapshot

必须支持：

```text
snapshot/
 ├── runtime_state
 ├── resource_state
 ├── event_cursor
 ├── plugin_state
 └── device_state
```

---

# 9.4 Time Travel Replay

例如：

```text
Replay to:
WakeDetected -2s
```

用于：

- flaky 分析
- timing 分析
- root cause

---

# 10. Device Simulation Layer

# 10.1 为什么必须 Simulation

后期 replay：

如果依赖真实：

- 云服务
- 网络
- ASR
- OTA

Replay 永远不稳定。

---

# 10.2 推荐 Simulation

```text
Fake ASR
Fake Cloud
Fake Media
Fake OTA
```

---

# 10.3 Simulation 能力

必须支持：

- latency inject
- packet loss
- timeout
- malformed response
- partial response

---

# 11. Assertion DSL

# 11.1 YAML 不够表达复杂时序

后期 assertion：

会越来越复杂。

YAML 会逐渐不可维护。

---

# 11.2 推荐 Validation DSL

例如：

```text
EXPECT WakeDetected WITHIN 3s
FORBID RebootDetected AFTER ASR FOR 10s
EXPECT MediaStarted AFTER TTSStarted
```

---

# 11.3 DSL Compiler

最终：

```text
DSL
 ↓
IR
 ↓
Runtime
```

---

# 12. Distributed Runtime

# 12.1 后期一定会分布式

因为：

- 多设备
- 多机房
- 多固件
- 大规模 regression

---

# 12.2 推荐结构

```text
Scheduler
   ↓
Worker Runtime
   ↓
Device Pool
```

---

# 12.3 Runtime Worker

每个 Worker：

- 独立 event bus
- 独立 replay
- 独立 state

禁止共享 runtime state。

---

# 13. Failure Exploration Engine

# 13.1 Agent 真正价值

Agent 不应该：

```text
决定 PASS/FAIL
```

真正价值：

# 探索状态空间。

---

# 13.2 推荐能力

```text
发现 flaky
 ↓
自动 mutation scene
 ↓
自动 perturb timing
 ↓
自动探索 failure neighborhood
```

---

# 13.3 Failure Neighborhood

例如：

```text
wake
 ↓
network_lost
 ↓
tts_interrupt
```

周围状态空间自动探索。

---

# 14. Capability Runtime 增强

# 14.1 Capability 不应只是 bool

当前：

```yaml
online_asr: true
```

太弱。

---

# 14.2 推荐 Capability Schema

```yaml
capabilities:
  asr:
    online: true
    offline: true
    latency_ms: 300

  media:
    codec:
      - mp3
      - wav
```

---

# 15. Runtime Isolation

# 15.1 Plugin 必须隔离

否则：

一个 plugin 崩溃：

整个 runtime 崩。

---

# 15.2 推荐 Sandbox

例如：

```text
plugin sandbox
```

支持：

- timeout
- memory limit
- crash isolate

---

# 16. Analytics Pipeline

# 16.1 Analytics 必须流式化

不要：

```text
日志离线分析
```

推荐：

```text
Event Stream
 ↓
Analytics Pipeline
```

---

# 16.2 推荐指标

| 指标 | 含义 |
|---|---|
| wake_latency | 唤醒耗时 |
| tts_latency | TTS 耗时 |
| media_interrupt_rate | 打断率 |
| reboot_frequency | 重启率 |
| flaky_score | flaky 概率 |

---

# 17. 推荐最终目录结构

```text
validation-platform/
 ├── kernel/
 ├── plugins/
 │    ├── wake/
 │    ├── media/
 │    ├── asr/
 │    ├── network/
 │    └── ota/
 ├── ir/
 ├── dsl/
 ├── replay_vm/
 ├── scene_engine/
 ├── constraint_engine/
 ├── analytics/
 ├── scheduler/
 ├── resource_runtime/
 ├── simulation/
 ├── adapters/
 ├── datasets/
 ├── device_pool/
 └── llm_tools/
```

---

# 18. 推荐演进顺序（非常关键）

# Phase 1

必须：

- Runtime Plugin 化
- Event Schema 正式化
- Monotonic Timeline

---

# Phase 2

必须：

- Constraint Scene Engine
- Resource Runtime
- Hierarchical StateMachine

---

# Phase 3

必须：

- Replay VM
- Snapshot
- Time Travel Replay

---

# Phase 4

必须：

- Simulation Layer
- Fake Cloud
- Fake ASR

---

# Phase 5

必须：

- Validation DSL
- DSL Compiler

---

# Phase 6

必须：

- Failure Exploration Engine
- Agent Mutation
- Failure Neighborhood Analysis

---

# 19. 最终目标

最终系统应该具备：

| 能力 | 说明 |
|---|---|
| Validation Kernel | 内核化 Runtime |
| Plugin Runtime | 插件化 |
| Event Graph | 图化 Timeline |
| Replay VM | 可回放虚拟机 |
| Constraint Scene Engine | 约束场景引擎 |
| Resource Runtime | 资源调度 |
| Device Simulation | 设备模拟 |
| Failure Exploration | 故障探索 |
| Assertion DSL | 断言语言 |
| Distributed Runtime | 分布式运行 |

---

# 20. 最终结论

当前 Validation Runtime 方向已经正确。

但：

# 后期最大的风险：

不是功能不够。

而是：

# Runtime 膨胀。

因此后续必须重点推进：

- Kernel 化
- Plugin 化
- Event Graph 化
- Replay VM 化
- Constraint 化
- Resource 化

否则：

系统会逐渐演变为：

```text
大型不可维护 Runtime
```

完成本方案后，系统会真正从：

```text
自动化测试平台
```

演进为：

# Enterprise Embedded Validation Runtime

进一步：

# Embedded Validation Operating System


---

# 21. 当前 skill 的执行裁剪

根据当前 Polaris 落地目标，先按以下顺序执行：

1. 已开始落地 Phase 1：Runtime Plugin 化、Event Schema 正式化、Monotonic Timeline。
2. 下一步再做 Constraint Scene Engine、Resource Runtime、Hierarchical StateMachine。
3. Replay VM、Simulation Layer、Assertion DSL、Failure Exploration 作为后续增强，不一次性重构。
4. 外部调度、远程设备池、大规模分布式执行暂不纳入当前 skill。

当前优先目标是：本地真机、Cucumber task、Event Runtime、Replay、断言报告稳定闭环。
