# 在线混合交互压测验证包

资料来源：`docs/wiki/voice-validation/` 与当前 Cucumber/Adapter/Event Runtime。本文是可复用验证包，供需求解读、方案/用例生成、执行入口选择和结果归因使用。

## 功能意图

验证设备在长时间在线交互中，基础命令、音乐、相声、新闻、烹饪问答、百科问答、组合场景随机混合执行时，是否存在重启、crash、watchdog、媒体异常、在线链路异常或额外识别。

## 前置条件

- 设备在线，Wi-Fi/热点稳定，API 环境与设备端环境一致。
- 声卡、PA、串口日志和云端/媒体响应 marker 可用。
- 压测轮次、截止时间、随机 seed、类别权重明确。

## 用例矩阵

| ID | 类型 | 场景 | 核心断言 | 自动化状态 |
| --- | --- | --- | --- | --- |
| OMS-001 | 正例 | 基础命令在线交互 | wake/ASR/command/响应闭环 | 已可执行 |
| OMS-002 | 正例 | 音乐/相声/新闻媒体交互 | TTS/media/player start/complete 或可解释 WARN | 已可执行 |
| OMS-003 | 正例 | 烹饪/百科问答 | 在线 ASR 与 TTS/媒体响应证据 | 已可执行 |
| OMS-004 | 稳定性 | 随机混合长稳 | 无 reboot/crash/watchdog；异常轮次可追溯 | 已可执行 |
| OMS-005 | 异常 | 媒体 HTTP timeout/云端短暂失败 | 有播放证据可 WARN；完全无响应按链路归因 | 已可执行 |
| OMS-006 | 反例 | 非目标窗口出现 wake/ASR/command | 记录误唤醒/误识别/自激候选 | 已可执行 |

## 统计与断言

- 统计总轮次、PASS/FAIL/BLOCKED/WARN、类别分布、连续失败、额外识别、媒体 error、重启/crash/watchdog。
- 不能只看云端返回；要检查设备侧 TTS/media/player 事件。
- HTTP recv timeout 但已有媒体播放证据时优先 WARN；没有任何播放证据时归媒体链路。

## 执行入口

- task：`satellite/cucumber-agent-testing/tasks/examples/online_mixed_stress.example.json`。
- WS63 task：`satellite/cucumber-agent-testing/tasks/examples/online_mixed_stress.ws63.example.json`。
- runner：`satellite/cucumber-agent-testing/scripts/run_online_mixed_stress.py`。
- 分析：`satellite/cucumber-agent-testing/scripts/analyze_online_stress.py`。
- strategy：`satellite/cucumber-agent-testing/references/scene_strategy_pool.json` 的 `online_mixed_stress`。
