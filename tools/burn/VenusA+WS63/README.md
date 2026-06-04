# VenusA + WS63 自动烧录说明

本目录用于 VenusA/CSK 与 WS63 固件烧录。当前 WS63 台架串口必须以 `polaris.local.json` 和用户现场确认为准，不能再沿用旧 WB01/组合拓扑默认值。

## 当前 WS63 串口基线

- WS63 AP/环境/版本口：`COM20 @ 921600`
- WS63 upper/asr 日志口：`COM17 @ 921600`
- WS63 控制口：`COM19 @ 115200`
- WS63 BurnTool 信号波特率：默认 `1000000`，端口默认使用 `COM17`；如现场确认烧录口为 `COM20`，必须显式设置 `WS63_BURN_PORT=COM20` 或命令行 `--ws63-port COM20`。
- 旧 `COM12/COM13/COM11` 只属于 WB01/历史组合烧录拓扑，不是当前 WS63 默认值；相关历史证据不能作为当前 WS63 `COM20/COM17/COM19` 的通过结论。

## 当前能力

- VenusA/CSK 烧录不再只看进程返回码，会解析 `Uart_Burn_Tool` 输出中的成功/失败 marker。
- 发现 `CONNECT ROM FAILED`、`HEX SEGMENT x/y FAILED`、`MD5 FORMAT ERROR`、`RESPONSE OVERTIME` 等失败标记时立即阻断，不继续烧 WS63。
- WS63 烧录同时校验 BurnTool `optLog_*.txt` 的 `烧写结果：成功` 与设备重启日志中的 `ListenAI Build Info`。
- 固件版本、生成时间、VenusA/WS63 BuildInfo 会从 `BuildInfo.txt` 与 `Other/WS63_build_*.log` 自动提取。
- 每次运行默认生成 artifact 目录，包含 console log、optLog 副本、BuildInfo 校验、`burn_summary.json` 和 `burn_summary.md`。

## 常用命令

默认入口只烧 WS63，并使用当前 WS63 串口基线：

```bat
run.bat
```

只烧 WS63，并使用默认 `.00.01` 固件恢复：

```bat
run_ws63_only.bat
```

只烧 WS63，并指定 `.00.02` 固件：

```bat
run_ws63_only.bat ..\ws63_fw\Midea_VenusA_WS63_35.03.01.01.18.26.05.04.00.02_20260526_200125
```

如果现场确认 WS63 BurnTool 应走 AP 口而不是 upper/asr 口，先显式覆盖端口再执行：

```bat
set WS63_BURN_PORT=COM20
set WS63_VERIFY_PORT=COM20
run_ws63_only.bat
```

dry-run 检查命令、固件路径和留证链路：

```bat
python auto_burn.py --firmware-root ..\ws63_fw\Midea_VenusA_WS63_35.03.01.01.18.26.05.04.00.01_20260519_021817 --control-port COM19 --ws63-port COM17 --ws63-verify-port COM17 --skip-venusa --dry-run
```

完整烧录 VenusA/CSK + WS63 必须先显式设置当前真实 `VENUSA_PORT`，脚本不再默认 `COM13`：

```bat
set VENUSA_PORT=<当前确认的VenusA或CSK烧录口>
run_full_venusa_ws63.bat
```

## 判定口径

- VenusA/CSK：返回码为 0 但出现失败 marker 仍判 FAIL。
- WS63：BurnTool 成功但设备侧 BuildInfo 不匹配仍判 FAIL。
- 只烧 WS63 时不代表 VenusA/CSK 烧录链路通过。
- 如需临时关闭某项校验，可使用 `--no-verify-ws63-build` 或 `--no-verify-venusa-version`，但报告中必须说明跳过原因。
- 2026-06-02 使用 `COM12/COM13/COM11` 得到的烧录记录仅归档为旧/组合拓扑证据；当前 WS63 `COM20/COM17/COM19` 已补做 `.00.01` WS63-only、`.00.02` WS63-only、恢复 `.00.01` 和 VenusA/CSK + WS63 full-burn 真机验证，烧录与版本校验均 PASS。
- 2026-06-02 full-burn 已验证端口组合：VenusA/AP `COM20@3000000/921600`、WS63 BurnTool/verify `COM17@1000000/921600`、control `COM19@115200`；执行后仍需恢复 `env=1/UAT`、云控基线和 first_wake/basic smoke。
- VenusA/CSK 失败阻断曾出现 `HEX SEGMENT 3/5 FAILED` 后设备侧 `Exception on CORE0 / Illegal instruction`，不要把 VenusA 失败注入当作低风险 smoke。
