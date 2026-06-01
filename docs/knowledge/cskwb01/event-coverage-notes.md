# WB01 项目 Event Graph 与 Coverage 记录

WB01 有 AP/CP/ASR/control 四串口，后续在线媒体、重启、误识别日志优先用三端互证。

## 当前状态

- 已在 `state_assertion_policy.json` 的 `coverage.projects.cskwb01` 预留项目覆盖位置。
- 已在 `event_graph_rules.json` 预留项目私有规则示例，默认 disabled，等待真实日志后启用。
- 2026-05-28 已执行 FA2 全量 343 条命令 baseline：`satellite/cucumber-agent-testing/debug/goal_command_beep/20260528_201015/runs/cskwb01_full_baseline/20260528_224149`。
- 本轮结果：PASS=147、PASS_WITH_WARNINGS=145、FAIL=51；主要 FAIL 为 `unexpected_recognition` 和 `command_domain_or_cloud_skill`，说明目标未命中或串扰候选较多。
- 本轮链路特点：WB01 本地关键词可观测性强，`offline_asr_callbak keyword` / pinyin keyword 可作为识别强证据；但大量窗口有额外命令词，需要作为误识别候选保留。
- 蜂鸣器/执行反馈：现有日志存在弱播放器/ACK 类 marker，但没有稳定明确的 `beep/buzzer/蜂鸣` marker，不能把弱 marker 直接等价为物理蜂鸣器 PASS；需要声学回采、项目私有 ACK 或人工 `manual_observed` 补强。
- 失败子集复验结果：baseline 51 条 FAIL 经复验、同义词/拼音规则修正后，最终稳定 FAIL=1；`调温循环扇反集` 被稳定识别为 `tiao wen xun huan shan dang wei fan ji`，更像需求词表/同义词口径或命令文案问题，需和需求确认“反集/档位反集”是否同义。

## 新日志分析清单

| 项 | 需要记录 |
| --- | --- |
| 证据路径 | debug run 目录、replay package、原始串口日志。 |
| 事件链 | Wake/ASR/Command/TTS/Media/Network/Reboot/Crash 的时间顺序。 |
| 私有 marker | 项目日志中能代表 TTS、MP3、播放器、boot reason、API env 的字段。 |
| 规则建议 | Event Graph relation、within_ms、confidence、是否 enabled。 |
| coverage 建议 | required/forbidden event、min_transition_count、项目 profile 阈值。 |

## 启用门槛

没有明确协议或强 marker 前，不启用“蜂鸣器必响”强规则；只保留候选和缺口，避免把项目猜测写成固件断言。
