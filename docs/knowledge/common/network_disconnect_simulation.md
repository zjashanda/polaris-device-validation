# 断网/未联网模拟与恢复控制策略

本文用于所有需要“断网、未联网、联网恢复、弱网前置”的真机场景。原则是先确认当前 DUT 的实际联网路径，再选择可控方案；不能只按电脑网络状态推断设备已经断网。

## 先做前置识别

1. 读取设备身份与 IP：
   - AP/CSK 串口执行 `deviceinfo`，记录 `IoT ID`、`Mac`、`IP`。
2. 查询本机热点：
   - `python tools\device\polaris_network_orchestrator.py hotspot-status`
   - 如果热点客户端列表中有 DUT MAC，说明可以用本机热点控制断网/恢复。
3. 判断是否外部网络：
   - 如果设备有 IP，但本机热点为 `Off` 或无 DUT MAC，说明设备大概率连接外部 Wi-Fi/路由器。
   - 此时不能用本机热点开关模拟断网；除非另有外部路由器/AP 控制能力。

## 方案选择

| 场景 | 推荐方案 | 可执行入口 | PASS/BLOCKED 口径 |
|---|---|---|---|
| DUT 连接本机 Windows 热点 | 热点关闭/开启/循环 | `hotspot-set --enable 0/1`、`hotspot-cycle` | 热点状态变化 + 串口网络断开/恢复 marker + 在线语音/云控 smoke |
| DUT 支持写入 `vir_ssid/vir_pwd` | 写错 SSID/密码后重启模拟未联网，写回正确配置后重启恢复 | `vir-reboot --ssid <ssid> --pwd <pwd>`；项目专用串口命令需先确认 | 必须有写入回显、重启日志、离线/上线 marker |
| DUT 连接外部 Wi-Fi/路由器且可控 | 控制外部 AP/路由器/上游网络 | 路由器电源、SSID 开关、MAC 黑名单/ACL、防火墙、上游网口断开等项目化入口 | 必须记录外部控制动作与设备侧断网/恢复证据 |
| DUT 连接外部 Wi-Fi 但不可控 | 只做在线能力验证，断网/恢复用例 BLOCKED/SKIPPED | 无自动断网动作 | 不能把电脑热点关闭当作设备断网证据 |

## 本机热点控制

```powershell
python tools\device\polaris_network_orchestrator.py hotspot-status
python tools\device\polaris_network_orchestrator.py hotspot-set --enable 0
python tools\device\polaris_network_orchestrator.py hotspot-set --enable 1
python tools\device\polaris_network_orchestrator.py hotspot-cycle --off-wait 15 --on-wait 45
```

Adapter 入口：

```powershell
python satellite\cucumber-agent-testing\scripts\run_adapter_action.py --env-file polaris.local.json --adapter-id network.local --action hotspot_off --execute --allow-side-effects
python satellite\cucumber-agent-testing\scripts\run_adapter_action.py --env-file polaris.local.json --adapter-id network.local --action hotspot_on --execute --allow-side-effects
```

注意：这些命令会真实影响 DUT 联网，执行前必须确认允许网络副作用。

## `vir_ssid/vir_pwd` 改写

已有能力记录为“`vir_ssid / vir_pwd` 改写后重启验证”。适用于设备固件支持把目标路由信息写入 flash/虚拟配网字段的项目。

常见入口：

```powershell
python tools\device\polaris_network_orchestrator.py vir-reboot --ssid <wifi_ssid> --pwd <wifi_password>
```

使用规则：

- 模拟未联网时，可以写入不存在的 SSID 或错误密码，再重启并观察离线 marker。
- 恢复时必须写回真实 SSID/密码，再重启并等待上线。
- 不同项目的串口命令可能不同；未确认命令前禁止盲目写 flash。
- 报告必须保留写入回显、`flash.show`/等价读回、重启窗口、离线/上线窗口日志。

## 外部路由器/AP 控制

当设备连接外部 Wi-Fi（例如设备 IP 不在本机热点网段，或本机热点无 DUT 客户端）时，只有拿到外部网络控制能力才能自动模拟断网。可选手段包括：

- 控制路由器/AP 电源：通过可控插座、电源通道或控制口上电/断电。
- 关闭/恢复 SSID：通过路由器管理 API、CLI 或 Web 自动化。
- MAC 黑名单/ACL：把 DUT MAC 加入黑名单，恢复时移除。
- 防火墙/上游阻断：阻断 WAN、DNS、云端目标域名或端口。
- 网络整形：限制带宽、延迟、丢包，用于弱网，不等同完全断网。

这些方案必须先形成项目专用 adapter/action 或明确人工步骤；没有外部控制证据时，断网/联网恢复用例应标记 `BLOCKED_NETWORK_NOT_CONTROLLABLE`。

## 设备侧证据

断网不能只看控制动作，至少要结合以下证据之一：

- 热点/路由器控制动作成功。
- AP/upper 日志出现 `wifi offline`、`AI disconnected`、`wifiLink_update:disconnect close`、`cloud status` 离线态等。
- 离线语音路径出现 `offline_wakeup`、`offline_asr_callbak`，且在线请求/云控不再成功。
- 恢复后 `deviceinfo` 有 IP，云控 env gate PASS，在线语音或云控 smoke PASS。

证据不足时使用 `BLOCKED`、`EVIDENCE_GAP` 或 `WARN`，不要判固件 FAIL。

## 2026-06-01 WS63 实例

最近 WS63 真机验证中，`deviceinfo` 返回 DUT IP `192.168.2.5`，MAC `60:7A:D8:1A:67:26`；本机 Windows 热点即使为 `On`，客户端数量也为 0，因此该 DUT 实际走外部 Wi-Fi/路由网络。

本场景结论：

- 不能通过关闭本机热点或电脑 Wi-Fi 来证明 WS63 已断网。
- `network_recovery_basic` 应记录为 `BLOCKED_NETWORK_NOT_CONTROLLABLE`。
- 若后续要自动验证 WS63 未联网/恢复，需要补充外部路由器/AP 控制入口，或确认固件支持 `vir_ssid/vir_pwd` 改写重启并能读回。
- 在线能力、云控和压测仍可在外部 Wi-Fi 正常在线时执行，但报告中必须标明断网前置不可控。
