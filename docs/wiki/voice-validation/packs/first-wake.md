# 首次唤醒验证包

资料来源：`docs/wiki/voice-validation/` 与当前 Cucumber/Adapter/Event Runtime。本文是可复用验证包，供需求解读、方案/用例生成、执行入口选择和结果归因使用。

## 功能意图

验证设备处于待唤醒状态时，播放目标唤醒词后能够进入识别态，并在 AP/CP/ASR 或 AP/upper 日志中形成唤醒闭环。该验证包不把命令识别失败混入首次唤醒分母。

## 前置条件

- `polaris.local.json` 中当前项目串口、声卡、唤醒词配置正确。
- 设备不在上一轮识别窗口内；如果只能靠间隔控制，播放间隔要大于识别超时。
- 声卡播放链路可用；播放成功但设备无任何唤醒证据时，先按项目规则检查 PA。
- managed session 已采集 AP/CP/ASR 或 AP/upper 日志。

## 用例矩阵

| ID | 类型 | 场景 | 核心断言 | 自动化状态 |
| --- | --- | --- | --- | --- |
| WK-001 | 正例 | 待唤醒状态播放一次唤醒词 | 播放返回 0；窗口内出现 wake marker；无 reboot/crash | 已可执行 |
| WK-002 | 异常 | 目标声卡不存在或播放失败 | BLOCKED，不进入唤醒率分母 | 已可执行 |
| WK-003 | 异常 | 串口 logger 无日志 | BLOCKED，先修串口/采集 | 已可执行 |
| WK-004 | 异常 | 播放成功但 AP/CP/ASR 均无 wake | 归音频链路/麦克风/固件唤醒候选 | 已可执行 |
| WK-005 | 稳定性 | 多轮首次唤醒率压测 | 统计成功率、未唤醒率、连续失败、异常重启 | 可执行但需配置轮次 |
| WK-006 | 边界 | 上一轮识别态未退出就播放 | 不纳入首次唤醒；转识别模式唤醒或时序问题 | 可执行但需日志判态 |

## 统计与断言

- 首次唤醒率 = 首次唤醒成功次数 / 首次唤醒有效总次数 * 100%。
- 未唤醒率 = 未唤醒次数 / 首次唤醒有效总次数 * 100%。
- 额外 wake marker、异常唤醒词 ID、连续失败必须记录。
- 播放锚点优先使用有效音频起点；长播放或临界超时无法确认时标记 `TIMING_AMBIGUOUS`。

## 执行入口

- Cucumber tag：`first_wake`。
- task：`satellite/cucumber-agent-testing/tasks/examples/first_wake.example.json`。
- 压测 runner：`satellite/cucumber-agent-testing/scripts/run_wake_stress.py`。
- Runtime profile：`first_wake`。

## 失败归因

1. 声卡/PA/音量问题：播放失败、目标声卡缺失、PA 未开。
2. logger 问题：串口打不开、无日志、端口映射错误。
3. 设备状态问题：并非待唤醒状态，上一轮未退出。
4. 固件/设备问题：播放成功、状态有效、日志完整但无唤醒证据。
