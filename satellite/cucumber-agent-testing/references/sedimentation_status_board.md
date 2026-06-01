# Polaris 沉淀状态板

## 已固化

| 产物 | 用途 |
| --- | --- |
| `references/extended_test_item_sedimentation.md` | 56 个测试项分级与资料缺口 |
| `references/sedimentation_execution_plan.md` | 沉淀执行计划 |
| `references/sedimentation_batch1_registry_draft.json` | 第一批 14 个能力 contract 草案 |
| `references/sedimentation_registry_extensions.static.json` | 可审阅的静态 registry extension |
| `references/polaris_sedimentation_batch1_feature_draft.md` | Cucumber 场景草案 |
| `scripts/ingest_requirements_corpus.py` | 从 `docs/requirements/` 抽取命令词/自由说/在线语料 |
| `scripts/discover_interrupt_prerequisites.py` | 生成天气/播歌/离线最长播报打断前置候选 |
| `scripts/build_sedimentation_registry_draft.py` | 将能力草案和语料 corpus 转为 registry extension |
| `scripts/run_sedimentation_pipeline.py` | 一键编排需求抽取、打断候选、registry draft 生成 |
| `scripts/build_requirement_oracle_draft.py` | 将需求语料 corpus 转为 oracle 草案和 smoke selection |
| `scripts/measure_interrupt_prerequisites.py` | 真机测量打断前置自播 start-end、时长和注入点 |
| `scripts/run_interrupt_injection.py` | 复用测得前置执行自播中唤醒/命令注入，并区分时序不明确 |
| `scripts/run_network_recovery_basic.py` | 执行热点断开/恢复、ensure-online 和在线语音 smoke |
| `scripts/run_oneshot_matrix.py` | 执行 wake+command one-shot 间隔矩阵并逐间隔归因 |
| `scripts/run_false_wake_quiet.py` | 静默窗口监听误唤醒、串口缺失、重启/崩溃并归因 |
| `scripts/run_wake_matrix.py` | 执行唤醒响应时间、连续唤醒、随机间隔唤醒小矩阵并输出归因 |
| `scripts/run_online_vad_special.py` | 构造短句/停顿/长停顿在线语料，采集在线 ASR、VAD end、云端播报和文本覆盖证据 |
| `scripts/run_attribution_validator.py` | 复核 BDD summary、模块 summary 和原始日志 marker 的归因一致性 |
| `scripts/run_false_wake_playback.py` | 播放合成人声/白噪声干扰并监听误唤醒 marker、reboot/crash |
| `features/polaris_voice_core.feature` | 已包含核心能力、需求 smoke、打断前置和唤醒打断 Cucumber 场景 |

## 已沉淀能力数量

- 主测试项覆盖：56 个均已在 `extended_test_item_sedimentation.md` 中分级。
- 第一批 registry 能力：14 个。
- 支撑脚本：14 个。
- Active Cucumber 场景：21 个（核心 5 个 + 需求/自由说 smoke 2 个 + 打断前置测量 + 自播中唤醒/识别打断 + 联网恢复 + 离线/在线 one-shot 矩阵 + 静默误唤醒 + 唤醒响应时间/连续唤醒/随机间隔唤醒 + 在线 VAD 专项 + 归因一致性复核 + 合成人声/白噪声误唤醒）。

## 已执行结果

- 沉淀流水线已通过：`debug/sedimentation_pipeline/20260514_163409`。
- 需求语料抽取：`4163` 条候选，其中 command=`891`、free_speech=`3206`、online=`16`、wake=`4`、unknown=`46`。
- oracle 草案：`debug/oracle_drafts/20260514_163410`，formal_candidate=`4093`，needs_review=`66`，smoke_selection=`74`。
- 需求命令词小样本：`debug/runs/20260514_193422_execute`，5/5 PASS。
- 需求自由说探索性小样本：`debug/runs/20260514_193828_execute`，5/5 PASS，标记 exploratory。
- 打断前置测量：`debug/runs/20260514_191242_execute`，选择 `打开自动模式`，自播 `4167ms`，建议 `+1875ms` 注入。
- 自播中唤醒打断：`debug/runs/20260514_201132_execute`，PASS，注入命中 2 个自播窗口，注入后 CP/AP/ASR 均有唤醒证据。
- 自播中识别打断：`debug/runs/20260514_202934_execute`，PASS，注入命中 2 个自播窗口，注入后 CP 命令关键词 `kong tiao kai ji`。
- 联网恢复基础验证：`debug/runs/20260514_203948_execute`，PASS，offline_signal/hotspot_restarted/ensure_success/online_query_pass 均为 true。
- 离线 one-shot 间隔矩阵：`debug/runs/20260514_204731_execute`，PASS，500/800/1000ms 三个间隔均通过。
- 静默误唤醒基础监听：`debug/runs/20260514_205607_execute`，PASS，30s 静默窗口未观察到唤醒 marker，且未见 boot/crash marker。
- 在线 one-shot 间隔矩阵：`debug/runs/20260514_210141_execute`，PASS，ensure-online 成功，800/1000ms 两个间隔均观察到唤醒和在线 ASR/云端播报证据。
- 连续唤醒稳定性小样本：`debug/runs/20260514_212020_execute`，PASS，连续 5 段唤醒词无间隔播放，CP/AP/ASR 最小三端唤醒数 5/5，未见 reboot/crash。
- 随机间隔唤醒小样本：`debug/runs/20260514_212544_execute`，PASS，5/5 PASS，随机间隔约 1.132~2.093s；首跑发现 `line_wakeup/offline wakeup` 未被 ASR wake 计数，已修复后复测通过。
- 唤醒响应时间小样本：`debug/runs/20260514_213146_execute`，PASS，3/3 PASS；当前统计口径为 `host_command_start_to_first_serial_wake_marker` 粗略 proxy，未配置音频回采/硬件播放起点时不作为正式响应时间阈值判定。
- 归因一致性复核：`debug/runs/20260514_220422_execute`，PASS，扫描最近每个场景 21 个 run，未发现 ERROR 级 BDD/模块/原始日志归因不一致。
- 在线 VAD 专项小样本：`debug/runs/20260514_214656_execute`，PASS，4/4 PASS；短句、正常句、900ms 停顿、1500ms 长停顿均观察到在线 ASR 文本和 VAD end，coverage=1.0。
- 合成人声干扰误唤醒小样本：`debug/runs/20260514_220023_execute`，PASS，播放不含唤醒词的人声 TTS 干扰，唤醒 marker=0，未见 reboot/crash。
- 白噪声误唤醒小样本：`debug/runs/20260514_220120_execute`，PASS，20s 低幅度白噪声，唤醒 marker=0，未见 reboot/crash。

## 可直接复跑的入口

```powershell
python satellite\cucumber-agent-testing\scripts\run_sedimentation_pipeline.py
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag requirement_command_smoke --command-file <txt> --command-limit 5 --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag interrupt_prerequisite_measurement --command-limit 2 --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag wake_interrupt --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag command_interrupt --command-text 打开空调 --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag network_recovery_basic --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag offline_oneshot_matrix --command-text 打开空调 --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag false_wake_quiet_basic --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag online_oneshot_matrix --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag continuous_wake_smoke --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag random_interval_wake_smoke --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag wake_latency_smoke --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag online_vad_special_smoke --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag attribution_validator_smoke --allow-side-effects
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag false_wake_human_speech_smoke --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
python satellite\cucumber-agent-testing\scripts\run_cucumber.py --mode execute --tag false_wake_white_noise_smoke --device-key "VID_8765&PID_5678:9_2A847557_7_0000" --allow-side-effects --manage-session
```

## 下一步验收点

1. 扩展误唤醒非安静场景：合成人声/白噪声 smoke 已完成；正式人声噪、非人声噪、多点噪仍需要标准噪声素材/声场信息。
2. 扩展在线/自由说正式 oracle：对生成的 oracle 草案做抽样复核，决定哪些自由说语料可以从 exploratory 升级为 formal。
3. 精确响应时间需要音频回采或播放起点硬件 marker；当前只保留粗略 proxy。
4. 若要正式压测阈值，需要补充次数、成功率阈值、响应时间阈值和 VAD 截断容差。
