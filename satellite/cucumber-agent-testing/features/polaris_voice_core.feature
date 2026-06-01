# language: zh-CN
@polaris @voice-core @bdd @agent-testing
功能: Polaris 语音核心能力 BDD 验证
  为了把测试方案中的语音能力转成可执行的 Agent Testing 任务
  作为 Polaris 测试代理
  我需要用 Gherkin 场景描述前置、动作、证据和断言

  背景:
    假如 使用当前 Polaris 本地串口配置
    而且 使用默认播放声卡
    而且 所有调试输出写入 Cucumber 调试目录

  @P0 @wakeup @first_wake
  场景: 首次唤醒
    假如 设备处于待唤醒状态
    当 播放唤醒词
    那么 应观察到 CP/AP/ASR 唤醒证据
    而且 未唤醒、播放失败或串口日志缺失应被区分归因

  @P0 @wakeup @recognition_mode_wake
  场景: 识别模式下唤醒
    假如 设备已经通过第一次唤醒进入识别模式
    当 在识别超时窗口内再次播放唤醒词
    那么 应观察到识别模式内的再次唤醒证据
    而且 需要区分识别窗口退出、音频链路失败和固件不支持

  @P0 @duplex @half_duplex_recognition
  场景: 半双工识别
    假如 云端或本地配置已切换为半双工
    当 播放唤醒词并在半双工识别窗口内播放命令词
    那么 应观察到符合半双工预期的 ASR 和命令闭环
    而且 播报中不应响应的输入不能误判为命令词失败

  @P0 @duplex @full_duplex_recognition
  场景: 全双工识别
    假如 云端或本地配置已切换为全双工
    当 播放唤醒词并在全双工识别窗口内连续播放命令词
    那么 应观察到符合全双工预期的 ASR 和命令闭环
    而且 播报中可识别、可打断或可连续对话需要按需求口径断言

  @P0 @command @basic_command_recognition
  场景: 基础命令词识别
    假如 已准备命令词词表和固定唤醒词
    当 对每条命令词执行先唤醒再识别
    那么 每条命令词应观察到唤醒、ASR 和命令关键词证据
    而且 FAIL 需要区分命令未播、未唤醒、未识别、词表期望不一致和设备行为问题

  @P1 @requirements @command @requirement_command_smoke
  场景: 需求命令词小样本识别
    假如 已从需求文档抽取命令词候选并形成命令文件
    当 对候选命令词执行先唤醒再识别
    那么 应逐条给出识别闭环、期望匹配和失败归因
    而且 需求词表或 oracle 不明确时不能直接判固件 FAIL

  @P1 @requirements @free_speech @requirement_free_speech_smoke
  场景: 需求自由说小样本识别
    假如 已从需求文档抽取自由说候选并形成语料文件
    当 对候选语料执行先唤醒再识别
    那么 应逐条给出探索性识别闭环和意图证据
    而且 自由说缺少正式 oracle 时结论应标记为探索性或待复核

  @P1 @interrupt @interrupt_prerequisite_measurement
  场景: 打断前置自播测量
    假如 已准备天气、播歌或离线长播报候选
    当 逐个触发候选并解析设备自播 start-end 证据
    那么 应选出可用于打断注入的稳定自播前置
    而且 候选不可用、时长不足和日志缺失只阻塞打断主流程不能误判固件打断失败

  @P1 @interrupt @wake_interrupt
  场景: 自播中唤醒打断
    假如 已有可用的自播打断前置和安全注入点
    当 在设备自播窗口内播放唤醒词
    那么 应观察到新的唤醒证据或明确的打断响应
    而且 注入未落入自播窗口时应标记为时序不明确而不是固件失败

  @P1 @interrupt @command_interrupt
  场景: 自播中识别打断
    假如 已有可用的自播打断前置和安全注入点
    当 在设备自播窗口内播放命令词
    那么 应观察到新的 ASR 或命令识别证据
    而且 若当前模式不允许播报中识别，应区分需求口径、模式配置和固件问题

  @P1 @network @network_recovery_basic
  场景: 联网恢复基础验证
    假如 设备已连接到本地热点并可采集联网日志
    当 关闭热点后再恢复热点并等待设备重新在线
    那么 应观察到设备恢复在线并完成一次在线语音 smoke
    而且 热点、设备在线和在线语音链路问题需要分开归因

  @P1 @oneshot @offline_oneshot_matrix
  场景: 离线 one-shot 间隔矩阵
    假如 已准备唤醒词、命令词和 one-shot 间隔集合
    当 按不同唤醒后间隔播放命令词
    那么 每个间隔应输出唤醒和命令识别闭环结果
    而且 播放、串口、间隔策略和 ASR 问题需要分开归因

  @P1 @false_wake @false_wake_quiet_basic
  场景: 静默误唤醒基础监听
    假如 当前环境保持安静且不主动播放测试音频
    当 连续监听一段静默窗口
    那么 不应观察到唤醒相关日志
    而且 串口无日志、设备重启和环境噪声不能误归为固件误唤醒

  @P1 @oneshot @online_oneshot_matrix
  场景: 在线 one-shot 间隔矩阵
    假如 设备在线且已准备在线语料和 one-shot 间隔集合
    当 按不同唤醒后间隔播放在线语料
    那么 每个间隔应输出唤醒、在线 ASR 或云端播报证据
    而且 联网、云端、播放和间隔问题需要分开归因

  @P1 @wakeup @latency @wake_latency_smoke
  场景: 唤醒响应时间小样本
    假如 已准备唤醒音频时间线和串口时间戳
    当 多轮播放唤醒词并采集唤醒响应 marker
    那么 只应统计唤醒成功轮次的响应时间
    而且 未配置正式阈值时应输出平均值、最大值、最小值和超阈值候选而不是误判固件失败

  @P1 @wakeup @continuous @continuous_wake_smoke
  场景: 连续唤醒稳定性小样本
    假如 已准备连续唤醒音频和串口稳定性监控
    当 连续播放多段唤醒词音频
    那么 应输出连续唤醒证据、连续失败和设备稳定性结果
    而且 日志中断、重启或崩溃需要与唤醒识别失败分开归因

  @P1 @wakeup @random_interval @random_interval_wake_smoke
  场景: 随机间隔唤醒小样本
    假如 已准备随机间隔集合和唤醒音频
    当 按随机间隔多轮播放唤醒词
    那么 应逐轮输出唤醒闭环和随机间隔下的状态稳定性
    而且 临界超时或状态不一致应标记为测试时序风险而不是直接归固件失败

  @P1 @online @vad @online_vad_special_smoke
  场景: 在线 VAD 专项小样本
    假如 设备在线且已准备短句、停顿句和长停顿在线语料
    当 播放唤醒词后执行在线 VAD 专项语料
    那么 应输出在线 ASR、VAD end、云端播报和文本覆盖证据
    而且 文本截断容差未确认时应标记探索性待复核而不是直接判固件失败

  @P1 @attribution @attribution_validator_smoke
  场景: 归因一致性复核
    假如 已有 Cucumber run 目录和模块 summary 证据
    当 二次解析 BDD 汇总、模块汇总和关键原始日志
    那么 BDD 结论应与模块证据保持一致
    而且 发现脚本误判、前置阻塞或 oracle 缺失时应单独归因

  @P1 @false_wake @false_wake_human_speech_smoke
  场景: 合成人声干扰误唤醒小样本
    假如 已准备不包含唤醒词的合成人声干扰音频
    当 播放人声干扰并监听串口窗口
    那么 不应观察到唤醒 marker
    而且 合成语音只能作为基础 smoke，不能替代标准人声噪声场

  @P1 @false_wake @false_wake_white_noise_smoke
  场景: 白噪声误唤醒小样本
    假如 已准备白噪声干扰音频和安全播放音量
    当 播放白噪声并监听串口窗口
    那么 不应观察到唤醒 marker
    而且 标准非人声噪测试仍需补充噪声素材、声压和声场
