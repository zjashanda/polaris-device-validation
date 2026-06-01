# 基础命令词识别验证包

资料来源：`docs/wiki/voice-validation/` 与当前 Cucumber/Adapter/Event Runtime。本文是可复用验证包，供需求解读、方案/用例生成、执行入口选择和结果归因使用。

## 功能意图

验证设备在成功唤醒后，对基础命令词或需求命令词能够正确识别并触发预期动作/响应；同时统计拒识、串扰和误识别。

## 前置条件

- 命令词文件存在，例如 `docs/fa2命令词.txt` 或需求词表。
- 每条命令执行前设备已成功唤醒；未唤醒样本不进入命令词分母。
- 期望 oracle 明确：ASR 文本、意图、命令 marker、设备动作、TTS/media 响应至少一种。

## 用例矩阵

| ID | 类型 | 场景 | 核心断言 | 自动化状态 |
| --- | --- | --- | --- | --- |
| CMD-001 | 正例 | 单条基础命令识别 | wake 成功；ASR/command/动作证据符合 oracle | 已可执行 |
| CMD-002 | 正例 | 全命令文件批量执行 | 输出逐条结果、识别率、拒识率、串扰矩阵 | 已可执行 |
| CMD-003 | 反例 | 命令词反集/集外语料 | 不应触发正式命令或高风险动作 | 需正式反集 |
| CMD-004 | 边界 | 短词/长词/数字参数 | 按类型分组统计，不替代整体识别率 | 需分类规则 |
| CMD-005 | 异常 | 词表不可读或 oracle 缺失 | BLOCKED/NEEDS_REVIEW，不判固件 | 已可执行 |
| CMD-006 | 稳定性 | 长时间命令词循环 | 统计连续失败、重启、额外 ASR/command | 可执行但需轮次 |

## 统计与断言

- 命令词识别率 = 识别正确次数 / 有效命令词样本数 * 100%。
- 拒识率 = 识别为空次数 / 有效命令词样本数 * 100%。
- 集内串扰率 = 集内串扰次数 / 有效命令词样本数 * 100%。
- 未播放目标命令却出现命令 marker，记录为误识别候选。

## 执行入口

- Cucumber tag：`basic_command_recognition`。
- task：`satellite/cucumber-agent-testing/tasks/examples/basic_command.example.json`。
- 默认命令词文件：`docs/fa2命令词.txt`。
- Runtime profile：`basic_command`、`command_batch`。
