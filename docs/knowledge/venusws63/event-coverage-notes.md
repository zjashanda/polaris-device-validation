# WS63 项目 Event Graph 与 Coverage 记录

WS63 通常为 AP+upper/control，无 CP；coverage 规则不应强制要求 CP marker。

## 当前状态

- 已在 `state_assertion_policy.json` 的 `coverage.projects.venusws63` 预留项目覆盖位置。
- 已在 `event_graph_rules.json` 预留项目私有规则示例，默认 disabled，等待真实日志后启用。
- 暂无新增真实日志可支撑更严格项目阈值；后续有压测/异常 run 目录时，按 `docs/wiki/voice-validation/failure-feedback.md` 反哺。
- 2026-05-27 真机 smoke 发现：AP 口 `COM16` 曾被 Xshell 占用，导致 managed logger 无法打开 AP 口；此类问题应判为环境 `BLOCKED`，不能把 AP 证据缺失归因为固件失败。已在 execute preflight 中增加串口打开探测，提前识别 Xshell/串口助手/旧 logger 占用。
- 2026-05-28 WS63 `打开空调` 控制变量定位结论：`half/full`、`1.0s/1.5s/2.5s/3.5s` 均能看到 `online_wakeup` 与 ASR cmd 事件，但缺少 `online_asr_callbak text`、命令关键词、有效 TTS URL；AP 侧多为 `TTS url is null` / `TTS recv with `，因此当前按 `ac_command_or_oracle_not_full_duplex` 归因，不能直接判成“全双工识别坏”。
- 同轮对照中 `播报今日新闻` 在全双工 1s delay 可 PASS，证明在线链路和全双工窗口并非整体不可用。后续若要把 WS63 空调控制判 PASS，需要项目确认：空 TTS/null TTS + appliance 帧是否代表命令成功，或者提供能代表空调开关成功的项目私有 marker。
- 2026-05-28 17:14 补测空调域命令：`关闭空调`、`制热模式`、`制冷模式`、`查询空调模式` 在 WS63 全双工 1.5s 下均出现 wake + ASR cmd + 空 TTS/null TTS，但没有在线 ASR 文本、命令关键词、有效 TTS URL 或本轮媒体状态；同矩阵 `播报今日新闻` 仍 PASS。结论扩大为“WS63 空调域命令可观测证据缺失/断言口径未闭环”，不是单条 `打开空调` 特例。
- 2026-05-28 17:44/17:55 追加“历史成功词 + 半双工/全双工 + oneshot”复核：`空调开机`、`空调关机`、`制冷模式`、`制热模式` 与 `打开空调/关闭空调` 大多仍只见 wake/ASR cmd/null TTS；`打开空调` 半双工 split/oneshot 仅出现 `empty24042513.mp3`，无 `online_asr_callbak`、本地关键词、`DeviceControl` 或 `cloud.speech.reply`，因此已把控制类 oracle 调整为不把 empty TTS URL 当作空调控制 PASS。
- 2026-05-28 全量 FA2 343 条 baseline：`satellite/cucumber-agent-testing/debug/goal_command_beep/20260528_201015/runs/venusws63_full_baseline/20260528_204530`。结果 PASS=161、PASS_WITH_WARNINGS=174、FAIL=8；主要 WARN 为 `tts_response_chain`，说明大量命令已有识别和 `DeviceControl` 控制回复证据，但 TTS URL 为空/播报链路未闭环。
- 蜂鸣器/执行反馈：本轮未发现可直接作为物理蜂鸣器 PASS 的明确 `beep/buzzer/蜂鸣` marker。对 no-op、查询、不支持或响应语义非状态变化类命令，`actuator_feedback` 应为 `NOT_EXPECTED` 或 `NOT_REQUIRED`；对理论应改变状态的命令，缺少自动证据时保持 `UNKNOWN`。
- 失败子集复验结果：baseline 8 条 FAIL 全部转为 PASS 或 PASS_WITH_WARNINGS，稳定 FAIL=0；这些样本按偶发唤醒/识别时序或观测窗口问题处理，不沉淀为 WS63 稳定功能缺陷。

## 2026-06-01 WS63 专项真机验证更新

本轮只针对 WS63，证据根目录为 `satellite/cucumber-agent-testing/debug/ws63_issue_deep_validation_20260601/`，最终汇总为 `final_issue_deep_report/ws63_issue_deep_validation_final_summary.json`。

- 设备/环境：IoT ID `210006741088068`，MAC `60:7A:D8:1A:67:26`，IP `192.168.2.5`，env=`1/UAT`，AP `COM20@921600`，upper/asr `COM17@921600`，control `COM19@115200`，声卡 `VID_8765&PID_5678:9_27F546DA_3_0000`。
- command-control 扩大矩阵：`command_baseline_expanded/20260601_164158/`，preflight PASS、serial coverage FULL、12/12 FAIL，root_cause=`command_or_audio_baseline`；半双工 baseline 也 FAIL，因此不是全双工单点问题。
- online VAD：3 轮 x 4 候选 = 12/12 FAIL，归因 `wake_precondition_for_online_vad`，未观察到 reboot/crash。
- offline one-shot：发现并修复 runtime replay 假阴性；当 `OneshotMatrixSummary` 为 PASS 且 row 级 `OneshotIntervalPassed` 全 PASS 时，runtime replay 可接受 row 级证据闭环，同时保留 raw counter gap。修复后真机复跑 PASS，3/3 interval PASS。
- 全双工变体：continuous/media/exception 仍 FAIL 在命令闭环；timeout_boundary 阻塞于 voice preset；cloud full-duplex apply preflow PASS 不能替代行为闭环。
- online mixed stress：5 轮 FINISHED，3 PASS / 1 `WARN_NO_ONLINE_RESPONSE` / 1 `FAIL_NO_WAKE`，新闻轮 PASS。
- interrupt：wake/command interrupt 均为 `BLOCKED/TIMING_AMBIGUOUS`，因为注入未稳定落入自播保护窗口，不判固件失败。
- network recovery：当前设备走外部 Wi-Fi，本机热点开启但 0 客户端，归类 `BLOCKED_NETWORK_NOT_CONTROLLABLE`。
- final snapshot：coverage=1.0，unknown_fields=[]，最终云控恢复无 mode conflict/config apply gap。

新增判定口径：

- 设备控制/命令闭环失败必须分清 wake/audio baseline、命令域、云端 skill、TTS/media 和 oracle，不得仅因全双工配置存在而归因为全双工。
- VAD 专项必须先满足 wake precondition；前置唤醒不稳定时，扩大 VAD 样本只用于归因，不作为固件 VAD FAIL 的唯一证据。
- one-shot replay 优先使用“矩阵汇总 + row 级 interval artifact”的闭环证据，避免 raw counter 缺口造成假阴性。

## 新日志分析清单

| 项 | 需要记录 |
| --- | --- |
| 证据路径 | debug run 目录、replay package、原始串口日志。 |
| 事件链 | Wake/ASR/Command/TTS/Media/Network/Reboot/Crash 的时间顺序。 |
| 私有 marker | 项目日志中能代表 TTS、MP3、播放器、boot reason、API env 的字段。 |
| 规则建议 | Event Graph relation、within_ms、confidence、是否 enabled。 |
| coverage 建议 | required/forbidden event、min_transition_count、项目 profile 阈值。 |

## WS63 空调命令 oracle 候选

空调/设备控制必须按完整链路拆分，不再把“识别到了”直接等价为“整条控制成功”。

| 链路段 | PASS 证据 | 缺失时归因 |
| --- | --- | --- |
| 命令识别 | `online_asr_callbak text`、`MSpeech Cloud 3 evt asr`、本地 `algo info keyword`。 | ASR/命令词/音频/时序问题。 |
| 云端控制回复 | `cloud.instructions.audioBroadcast` 或 `cloud.speech.reply`，且 `mideaSkillId: DeviceControl` / `skillId:11042`。 | 云端技能/设备品类/绑定/环境问题。 |
| TTS/播报响应 | 有有效 TTS URL、播放器开始播放，或声学回采确认。 | `TTS url is null` / `no valid tts url` 时归因播报资源/TTS 链路，不应覆盖前两段结论。 |
| 执行/蜂鸣器反馈 | 项目私有执行 ACK、蜂鸣器日志 marker、声学回采，或人工标注 `manual_observed`。 | 没有自动证据时标记 actuator evidence gap，不能默认控制器已动作。 |

| 候选 marker | 当前判断 | 说明 |
| --- | --- | --- |
| `online_wakeup` + ASR cmd 0x1006/0x1005 | 必要但不充分 | 只能证明唤醒与 ASR 事件进入，不能证明 `打开空调` 被识别或执行。 |
| `TTS url is null` / `TTS recv with ` | 不作为成功响应 | 已在控制变量 runner 中单独计数为 null TTS，避免假 PASS。 |
| `TTS recv/playing with .../empty24042513.mp3` | 不作为空调控制成功响应 | 只能证明云端/播放器返回了空白音频资源，不能证明识别文本正确或空调执行成功。 |
| `MSpeech Cloud 3 evt` / `online_asr_callbak, text: ...` | 强证据 | 能证明云端识别文本；文本应和本轮命令词匹配或在可解释的同义词集合内。 |
| `algo info keyword` / `ignore local asr ... when cloud connected` | 强证据 | 能证明本地/离线命令词命中；即使云端连接时被忽略，也可作为“本地命令识别正常”的佐证。 |
| `cloud.instructions.audioBroadcast` / `cloud.speech.reply` 且 `mideaSkillId: DeviceControl` | 强证据 | 能证明云端设备控制技能有回复；若响应文案含“已开机/已关机/请先开机”等，可作为控制语义的主要断言。 |
| `device state recv, class: media(6), state: 1` | 仅对媒体类命令可作为响应 | 必须出现在本轮 wake 之后，不能把上一轮残留媒体状态计入当前命令。 |
| `appliance trans/recv` 底层帧 | 候选，默认不启用 | 需要项目协议或固件日志说明哪类帧代表空调开/关成功，否则只能作为辅助证据。 |
| WB01 `WAKE(0): KEY=1(kong tiao kai ji)` / `online_asr_callbak, text: 打开空调` | 对照证据 | WB01 能输出 strict oracle 需要的关键词/ASR 文本；WS63 当前缺少同等可观测证据。 |

## 启用门槛

没有真实日志前，不启用强规则；只保留候选和缺口，避免把项目猜测写成固件断言。
