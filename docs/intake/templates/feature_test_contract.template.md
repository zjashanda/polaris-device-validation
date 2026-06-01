# 功能测试契约模板

> 用途：当一个新功能要被沉淀为 Cucumber/Runtime 自动化时，先把它写成这份契约。我会根据契约生成或更新 feature、mapping、action、assertion、runtime profile 和 task。

## 1. 功能基本信息

- 项目 ID：
- 功能 ID：
- 功能名称：
- 资料来源：
- 适用设备/固件：
- 优先级：P0 / P1 / P2

## 2. 功能意图

说明这个功能到底验证什么。例如：

- 首次唤醒：待唤醒状态下播放唤醒词，设备应进入唤醒/识别窗口。
- 识别模式下唤醒：首次唤醒后未超时，在识别窗口内再次播放唤醒词，设备应能二次唤醒或刷新会话。
- 全双工：自播期间允许识别新语音，并在超时窗口内完成 ASR/命令闭环。

## 3. 前置条件

- 设备状态：
- 联网状态：
- 云环境：
- 串口/日志：
- 声卡/音频：
- API/本地命令：
- 其他：

## 4. 输入动作

| 步骤 | 动作 | 参数 | 时间/间隔 | 备注 |
| --- | --- | --- | --- | --- |
| 1 | 播放唤醒词 | 小美小美 | - | 示例 |

## 5. 期望证据

| 证据类型 | 期望 marker / 状态 | 来源 | 是否必须 |
| --- | --- | --- | --- |
| wake | `wakeup_callback` / `WAKE(1)` | AP/CP/ASR | 是 |
| ASR | `online_asr_callbak` | AP/upper | 按功能 |
| command | `WAKE(0)` / keyword | CP/AP | 按功能 |
| media | TTS URL / player play/complete | AP/upper | 按功能 |

## 6. 不允许出现的证据

- 未播放/未说的其他唤醒词被识别。
- 未播放/未说的其他命令词被识别。
- 非本轮语料的 ASR 文本。
- reboot / watchdog / panic / hardfault / assert。
- 串口 reader 错误。

## 7. 断言与归因

| 情况 | 结果 | 归因 |
| --- | --- | --- |
| 前置串口不可用 | BLOCKED | 环境/接线 |
| 播放失败 | BLOCKED | 声卡/音频 |
| 播放成功但无 wake | FAIL 或 BLOCKED | 结合 PA/麦克风/固件判断 |
| 有 wake 但 ASR 缺失 | FAIL/WARN | ASR/在线链路/时序 |
| 出现未期望识别 | FAIL/WARN | 误唤醒/误识别/串音/残留 |

## 8. Cucumber 草案

```gherkin
@P1 @project_id @feature_id
场景: 功能名称
  假如 前置条件
  当 执行动作
  那么 应观察到期望证据
  而且 不应观察到禁止证据
```

## 9. 仍缺的信息

- 缺少的串口/API/日志 marker：
- 缺少的需求阈值：
- 缺少的测试语料：
- 缺少的设备配置：
