# 半双工识别验证包

资料来源：`docs/wiki/voice-validation/` 与当前 Cucumber/Adapter/Event Runtime。本文是可复用验证包，供需求解读、方案/用例生成、执行入口选择和结果归因使用。

## 功能意图

验证设备处于半双工模式时，用户每次语音输入通常需要先唤醒；设备播报/TTS/媒体播放期间是否禁止识别或打断，必须符合需求定义。

## 前置条件

- 设备在线，云控/API 环境与设备端 UAT/SIT/PRO 一致。
- 半双工配置入口明确：云控 API、本地串口命令或项目私有配置。
- 已配置唤醒词、声卡、串口日志和在线响应 oracle。

## 用例矩阵

| ID | 类型 | 场景 | 核心断言 | 自动化状态 |
| --- | --- | --- | --- | --- |
| HD-001 | 正例 | 下发半双工配置 | API/本地命令成功，后续行为符合半双工 | 已有 flow |
| HD-002 | 正例 | 唤醒后说一条在线/命令词 | wake、ASR/command、TTS/media 或动作闭环 | 已可执行 |
| HD-003 | 反例 | 未唤醒直接说命令 | 不应被当作有效命令，若识别需记误识别候选 | 需补场景 |
| HD-004 | 边界 | 播报中说新命令 | 若需求禁止播报中识别，出现 ASR/command 为 FAIL；若需求允许则转全双工/打断 | 需需求口径 |
| HD-005 | 异常 | 半双工配置未生效 | BLOCKED/配置链路，不判识别算法 | 已有 flow |
| HD-006 | 稳定性 | 多轮半双工唤醒+命令 | 成功率、拒识、串扰、重启、媒体异常 | 可执行但需轮次 |

## 断言与归因

- 半双工配置成功和半双工识别成功分开判断。
- 未唤醒导致无 ASR，不进入命令识别分母。
- 播报中识别策略不明确时输出 `REQUIREMENT_REVIEW`。
- API 成功但设备端环境未切对时归环境不一致。

## 执行入口

- Cucumber tag：`half_duplex_recognition`。
- Adapter flow：`switch_device_env`、`ensure_online`、`set_half_duplex`。
- Runtime profile：`half_duplex_recognition`。
