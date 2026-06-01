# 误唤醒验证包

资料来源：`docs/wiki/voice-validation/` 与当前 Cucumber/Adapter/Event Runtime。本文是可复用验证包，供需求解读、方案/用例生成、执行入口选择和结果归因使用。

## 功能意图

验证设备在未播放目标唤醒词时，不应产生 wake marker、误进入识别态或触发后续 ASR/command。误唤醒统计必须和正式唤醒率分开。

## 前置条件

- 串口日志连续可读；无日志时不能统计误唤醒。
- 环境类型明确：安静、人声干扰、非人声噪、白噪声、长期挂机等。
- 噪声/人声素材、播放音量、SNR 或测试时长有记录；合成数据只能做 smoke。

## 用例矩阵

| ID | 类型 | 场景 | 核心断言 | 自动化状态 |
| --- | --- | --- | --- | --- |
| FW-001 | 反例 | 安静环境监听 | wake/ASR/command 为 0；无 reboot/crash | 已可执行 |
| FW-002 | 反例 | 合成人声不含唤醒词 | 不应出现 wake；出现则记录样本 | 可执行但正式需素材 |
| FW-003 | 反例 | 白噪声/非人声噪 | 不应出现 wake；串口不断流 | 可执行但正式需 SNR |
| FW-004 | 异常 | logger 无日志或设备重启 | BLOCKED/稳定性异常，不计误唤醒 | 已可执行 |
| FW-005 | 稳定性 | 长期挂机 | 统计误唤醒频度、重启、日志中断 | 需测试时长 |
| FW-006 | 配置类 | 灵敏度高/低对比 | 分配置统计误唤醒频度 | 需配置入口 |

## 统计与断言

- 误唤醒频度 = 误唤醒次数 / 测试时长。
- 干扰触发率 = 干扰触发正式唤醒次数 / 有效干扰样本数 * 100%。
- 重启、logger 中断、声卡播放失败、素材无效都不进入误唤醒分母。

## 执行入口

- Cucumber tag：`false_wake_quiet_basic`、`false_wake_human_speech_smoke`、`false_wake_white_noise_smoke`。
- Runtime profile：`false_wake_quiet`、`false_wake_playback`。
- 额外识别记录：在线压测和命令词压测中所有非目标 wake/ASR/command 都要进入误唤醒/误识别候选。
