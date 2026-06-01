# 语音验证 Wiki

资料来源：`oldTime/legacy_20260526_144646/satellite/voice-test-plan-designer`。旧目录只作为归档来源，当前方案设计和用例生成应优先读取本 Wiki 与当前 Cucumber/Runtime registry。

## 当前页面

| 页面 | 用途 |
| --- | --- |
| `test-item-index.md` | 从旧测试项配置抽取的测试项总表、当前能力等级和缺口。 |
| `wakeup.md` | 首次唤醒、识别模式唤醒、打断唤醒、响应时间、连续/随机/多唤醒等方法。 |
| `command.md` | 基础命令词、反集/集外、one-shot、打断、自激、响应时间等方法。 |
| `free-speech.md` | 自由说意图、slot、说法、反集、口音语速、人群和响应时间。 |
| `online-recognition.md` | 半双工、全双工、在线 VAD、弱网、云端稳定性和在线响应时间。 |
| `false-wake.md` | 安静、人声噪、非人声噪、多点噪、灵敏度和长期挂机误唤醒。 |
| `test-data-design.md` | 语料、音频、样本量、间隔、场景参数和数据有效性规则。 |
| `assertion-attribution.md` | PASS/FAIL/BLOCKED/TIMING_AMBIGUOUS/需求复核的归因规则。 |
| `wiki-to-validation-pack-workflow.md` | 新资料如何从 intake 学习并变成 Wiki、验证包和执行入口。 |
| `new-project-feature-intake.md` | 新项目/新功能资料如何从 intake 沉淀到 Wiki、knowledge 和可执行 runtime。 |
| `failure-feedback.md` | 压测/真机异常如何反哺新用例、断言、Event Graph rule 和 coverage 阈值。 |
| `project-rule-overlays.md` | WB01/WS63/新项目私有 marker、Event Graph overlay 和 coverage 阈值沉淀规则。 |
| `packs/` | 可复用功能验证包，例如在线全双工。 |

## 工作原则

- Wiki 给方案和用例生成提供依据；真实执行仍走 Cucumber/Adapter/Runtime。
- 已支持功能应优先匹配已有验证包，不要每次从零写脚本。
- 未支持功能先补 Wiki + 验证包 + registry/task/runtime profile，再进入真机执行。
- 压测或真机异常要反哺 Wiki：新增失败模式、断言规则、专项用例或项目私有规则。
