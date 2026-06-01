# Polaris 第一批沉淀能力 Cucumber 草案

说明：本文件是受控自然语言草案，不直接放入 `features/` 避免影响现有 strict compile。待 step/action/assertion registry 生成并验证后，再拆入正式 `.feature`。

```gherkin
@sedimentation @wake @stability
Feature: 唤醒稳定性沉淀

  Background:
    Given 使用本地 Polaris 串口配置
    And 使用指定声卡播放测试音频
    And 开启串口日志采集

  @wake_continuous
  Scenario: 连续唤醒稳定性
    Given 设备处于可唤醒状态
    When 连续播放唤醒词 50 次
    Then 应统计连续唤醒成功率
    And 应统计最大连续未唤醒次数
    And 不应出现设备重启或串口日志中断

  @wake_random_interval
  Scenario: 随机间隔唤醒稳定性
    Given 设备处于可唤醒状态
    When 按 1 到 60 秒随机间隔播放唤醒词 50 次
    Then 应统计随机间隔唤醒成功率
    And 临界超时灰区样本应单独标记

  @wake_latency
  Scenario: 唤醒响应时间统计
    Given 设备处于待唤醒状态
    When 播放唤醒词并记录唤醒响应时间 20 次
    Then 只统计唤醒成功样本的响应时间
    And 输出平均值 最大值 最小值和超阈值样本
```

```gherkin
@sedimentation @command @requirements
Feature: 命令词需求抽取和覆盖沉淀

  @command_coverage
  Scenario: 从需求文档检查命令词覆盖
    Given 已从需求文档抽取命令词候选
    And 已读取可执行命令词测试集
    When 比对需求命令词和可执行测试集
    Then 应输出命令覆盖率
    And 应列出缺失命令和缺少期望 oracle 的命令

  @command_grouped
  Scenario: 短词长词数字类指令分组统计
    Given 已从需求文档抽取命令词候选
    When 按短词 长词 数字类对命令词分组
    Then 应生成分组测试任务
    And 应按分组输出识别率和串扰率

  @offline_oneshot
  Scenario Outline: 离线 oneshot 间隔矩阵
    Given 设备支持离线 oneshot 或标记为需求待确认
    When 播放唤醒词和命令词间隔 <interval_ms> 毫秒的 oneshot 音频
    Then 应按唤醒失败 命令未识别 命令错误 执行异常拆分失败原因

    Examples:
      | interval_ms |
      | 500         |
      | 800         |
      | 1000        |
      | 1500        |
```

```gherkin
@sedimentation @interrupt
Feature: 打断前置自发现

  @interrupt_prerequisite_discovery
  Scenario: 自动选择可用于打断的自播前置
    Given 设备可以执行在线或离线命令
    When 尝试查天气 播歌 和离线长播报命令
    Then 应选择播报时长最长且日志稳定的自播前置
    And 若没有可用自播前置应标记为 BLOCKED

  @wake_interrupt
  Scenario: 自播中唤醒打断
    Given 已选择稳定自播前置
    When 在自播过程中播放唤醒词
    Then 应判断自播是否被唤醒词打断
    And 应区分设备不支持打断和打断失败

  @command_interrupt
  Scenario: 自播中识别打断
    Given 已选择稳定自播前置
    When 在自播过程中播放第二条命令词
    Then 应判断第二条命令词是否被正确识别
```

```gherkin
@sedimentation @free_speech @requirements
Feature: 自由说需求抽取和口语化候选沉淀

  @free_coverage
  Scenario: 从需求文档检查自由说意图和 slot 覆盖
    Given 已从需求文档抽取自由说候选
    When 统计意图和 slot 覆盖
    Then 应输出意图覆盖率和 slot 覆盖率
    And 缺少 oracle 的说法应标记为 NEEDS_REVIEW

  @free_paraphrase
  Scenario: 生成前缀后缀插入语候选
    Given 已从需求文档抽取基础说法
    When 生成前缀 后缀 插入语和口语化表达候选
    Then 候选语料应绑定原始意图
    And 未人工确认的候选不得作为正式 FAIL 依据
```

```gherkin
@sedimentation @online
Feature: 在线识别扩展沉淀

  @online_oneshot
  Scenario Outline: 在线 oneshot 间隔矩阵
    Given 设备已联网
    When 播放唤醒词和在线语料间隔 <interval_ms> 毫秒的 oneshot 音频
    Then 应按唤醒失败 在线 ASR 未识别 ASR 文本错误 网络云端异常拆分失败原因

    Examples:
      | interval_ms |
      | 500         |
      | 800         |
      | 1000        |
      | 1500        |

  @network_recovery
  Scenario: 联网恢复前置
    Given 当前设备可能离线
    When 执行热点和设备在线恢复
    Then 设备 MAC 应接入热点
    And 云端在线证据应满足在线识别前置
```

```gherkin
@sedimentation @attribution
Feature: 证据二次复核和失败归因

  @evidence_validator
  Scenario: 原始日志二次复核 runner 结论
    Given 已完成一次 Cucumber 测试运行
    When 重新解析原始串口日志 播放日志 和网络日志
    Then 应识别脚本误判
    And 应区分环境阻塞 固件算法问题 需求口径缺失
```

