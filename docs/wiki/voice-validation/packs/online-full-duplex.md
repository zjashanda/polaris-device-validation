# 在线全双工验证包

资料来源：`oldTime/legacy_20260526_144646/satellite/voice-test-plan-designer`。旧目录只作为归档来源，当前方案设计和用例生成应优先读取本 Wiki 与当前 Cucumber/Runtime registry。

## 1. 功能意图

验证设备在在线全双工模式下，首次唤醒后能够在识别态内连续接收在线语音；在 TTS/媒体/自播过程中是否允许继续识别或打断，必须符合需求定义；超时后不应把窗口外语音误判成窗口内有效识别。

## 2. 前置条件

- 设备在线，Wi-Fi/热点稳定。
- 设备端环境与 API 环境一致：UAT/SIT/PRO 必须先切设备端，并由工具复查到 `env` 一致后，再调用 API。
- 云控环境门禁必须通过：查询不到 env、设备仍在 PRO 但 API 配置为 UAT/SIT、或只切环境未重启/复查成功，均直接 `BLOCKED`，不进入全双工功能判定。
- 声卡可播放，必要时控制口执行 PA 恢复：`uut-pa.on`、`pa-enable.set 0 17 0 1`。
- 已有 AP/CP/ASR 或 AP/upper 日志采集。
- 已确认全双工开关入口：云控 API、本地串口命令或项目私有控制方式。

## 3. 用例矩阵

| ID | 类型 | 场景 | 主要步骤 | 核心断言 | 失败归因重点 | 自动化状态 |
| --- | --- | --- | --- | --- | --- | --- |
| FD-001 | 正例 | 全双工配置生效 smoke | 查 env -> 必要时切 UAT/SIT/PRO 并重启 -> 复查 env 一致 -> 下发全双工 -> 查询/观察配置 | API 成功且设备日志/后续行为证明生效 | API/设备环境/云端/配置 | 已有 task smoke |
| FD-002 | 正例 | 首次唤醒后在线语音识别 | 唤醒 -> 播放在线问答/天气/百科语料 | wake、online ASR、TTS/media 响应 | 唤醒、在线链路、媒体链路 | 可执行 |
| FD-003 | 正例 | 一次唤醒后连续两到三句 | 首次唤醒 -> 间隔小于识别超时连续播放多句 | 识别态保持，连续 ASR/响应符合需求 | 超时、VAD、会话状态 | 已有 continuous task，需真机阈值 |
| FD-004 | 正例/打断 | TTS/媒体自播中继续识别或打断 | 触发长播报/音乐/相声 -> 窗口内注入语音 | 注入点在自播窗口；识别/打断行为符合需求 | 自播窗口、媒体 oracle、需求口径 | 可基于 interrupt 前置扩展 |
| FD-005 | 边界 | 全双工超时后再说话 | 唤醒 -> 等待超过全双工/识别超时 -> 播放语料 | 不应按识别窗口内语音处理；需要重唤醒才有效 | 超时 marker、会话退出 | 已有 timeout_boundary task，需真机阈值 |
| FD-006 | 边界 | 临界超时保护 | 在超时前后 guard 区域注入语音 | guard 内不强判；输出 `TIMING_AMBIGUOUS` 或单独统计 | 播放锚点、音频时长、日志延迟 | 已有 timeout_boundary task，guard 需按项目确认 |
| FD-007 | 异常 | 设备离线/云不可达 | 断网或模拟云端失败后执行在线语料 | 不把云端/联网失败判成全双工算法 FAIL | 网络、云服务、热点 | 可执行但需允许联网副作用 |
| FD-008 | 异常 | API 环境不一致 | API 设 UAT/SIT 但设备端未切对应环境 | 应 BLOCKED/环境不一致，不判固件 | 设备 env、API env | 可 dry-run/人工验证 |
| FD-009 | 异常 | 声卡播放失败或 PA 未开 | 目标声卡不存在/播放无效时执行 | BLOCKED；提示 laid/PA 恢复，不进入功能分母 | 声卡、PA、音频链路 | 已有 adapter/laid/PA flow |
| FD-010 | 反例 | 未播放目标语音却出现 ASR/command | 监听窗口内不播放或播放反集 | 出现 ASR/command 记误识别候选 | 误识别、自激、环境噪声 | 已有 exception_matrix 静默入口，反集语料可扩展 |
| FD-011 | 稳定性 | 全双工过程中重启/crash/watchdog | 多轮在线全双工交互压测 | 无 reboot/crash/watchdog；异常需定位 boot reason | 固件稳定性/供电/日志 | 可纳入在线混合压测 |
| FD-012 | 稳定性 | 随机多轮全双工交互 | 问答、音乐、相声、新闻、命令随机混合 | 成功率、失败分类、额外识别、媒体响应、重启 | 长稳、云端、媒体、状态机 | 可执行，需配置轮次 |

## 4. 断言关注点

- 必须分清“全双工配置是否生效”和“全双工识别是否成功”。
- 必须保存额外 ASR/command/wake；没有说目标词却识别了，属于误识别/自激候选。
- 媒体响应要看 TTS/media/player start/complete/error，不能只看云端返回。
- 超时边界必须设置 guard，guard 内不用于强判固件问题。
- 任何离线、API 环境不一致或 env 未确认、声卡/PA 不可用、串口日志缺失，都先 BLOCKED。

## 5. 当前执行入口

- Cucumber tag：`full_duplex_recognition`。
- 示例 task：`satellite/cucumber-agent-testing/tasks/examples/online_full_duplex.example.json`。
- Adapter flow：`switch_device_env`、`ensure_online`、`set_full_duplex`、`wake_audio_file`、`pa_recover`。
- Runtime：Event Graph、StateMachine、coverage policy、media/network/reboot plugins。

## 6. 当前缺口

- FD-002 到 FD-012 已拆成 task/scene 参数化入口，可一键 dry-run/execute；后续重点是补充项目私有阈值与更多真机证据。
- 需要项目级全双工超时时间、播报中识别策略、媒体响应 oracle 和正式阈值。
- 需要更多 WB01/WS63 真机日志反哺项目私有 Event Graph rules 与 coverage 阈值。

## 7. FD-002~FD-012 执行入口拆分

| 用例 | 推荐入口 | 说明 |
| --- | --- | --- |
| FD-002/FD-003 | `online_full_duplex.continuous.example.json` | 首次唤醒后连续在线语句，验证会话保持和连续 ASR/响应。 |
| FD-004 | `online_full_duplex.media_interrupt.example.json` | 先触发长 TTS/媒体，再在窗口内注入语音，验证播报中识别/打断策略。 |
| FD-005/FD-006 | `online_full_duplex.timeout_boundary.example.json` | 超时后与临界 guard 场景，默认不强判固件，输出边界证据。 |
| FD-007/FD-008/FD-009/FD-010 | `online_full_duplex.exception_matrix.example.json` | 设备离线、环境不一致、声卡/PA、静默误识别等异常/反例。 |
| FD-011/FD-012 | `online_full_duplex.random_stress.example.json` | 全双工模式下随机在线混合压测，监控重启、crash、媒体和额外识别。 |
| FD-002~FD-012 总览 | `references/scenes/online_full_duplex_fd002_fd012.scene.example.json` | 可通过 `run_kernel_scene.py --print-command --emit-ir-bundle` 检查参数化 scene。 |

以上入口都必须和 `full_duplex_recognition` tag 保持关联；执行时优先走 `run_optimized_task.py` 或 `run_kernel_scene.py`，不要临时让大模型生成脚本。

