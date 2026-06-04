# WS63 自动烧录与校验知识

本文记录 `tools/burn/VenusA+WS63/` 的自动烧录口径，避免后续把“工具退出码”误当成“固件烧录成功”，也避免把历史 WB01/组合拓扑串口误用于当前 WS63。

## 当前 WS63 串口拓扑

- WS63 AP/环境/版本口：`COM20 @ 921600`
- WS63 upper/asr 日志口：`COM17 @ 921600`
- WS63 控制口：`COM19 @ 115200`
- WS63 BurnTool 默认端口：`COM17`，信号波特率默认 `1000000`；如现场确认烧录口为 AP `COM20`，必须显式传 `--ws63-port COM20 --ws63-verify-port COM20`。

纠偏规则：旧 `COM12/COM13/COM11` 来源于 WB01/历史 VenusA+WS63 组合烧录拓扑，不是当前 WS63 默认值。凡使用旧端口得到的结果，只能归档为历史/旧拓扑证据，不能作为当前 WS63 `COM20/COM17/COM19` 的自动烧录 PASS 结论。

## 成功判定

### VenusA/CSK

合格条件：

1. `Uart_Burn_Tool` 进程返回码为 0。
2. 输出中无失败 marker。
3. 输出中有可信成功 marker，除非人工明确使用 `--no-strict-venusa-markers` 并在报告中说明。
4. 如启用版本校验，必须使用当前确认的 VenusA/CSK shell 口读取 `version` 并匹配固件 `BuildInfo.txt`；没有当前口径时不要默认 `COM13`。

失败 marker 包括：

- `CONNECT ROM FAILED`
- `HEX SEGMENT x/y FAILED`
- `MD5 FORMAT ERROR`
- `RESPONSE OVERTIME`
- 其他明确 `FAILED`/`FORMAT ERROR`

### WS63

合格条件：

1. BurnTool `optLog_*.txt` 出现 `烧写结果：成功`。
2. 退出工厂模式后重启设备。
3. 当前 WS63 验证口（默认 `COM17@921600`，或现场确认的 `COM20@921600`）启动日志中的 `ListenAI Build Info` 命中固件包 `BuildInfo.txt` 或 `Other/WS63_build_*.log` 中提取的 BuildInfo。
4. artifact 中保留 optLog 副本、启动日志和 `burn_summary.json`。
5. 烧录前后都要确认 `COM20/COM17/COM19` 与 `polaris.local.json` 一致；串口不一致时直接 BLOCKED，不盲烧。

## 常用入口

默认入口只走当前 WS63-only 路径：

```bat
tools\burn\VenusA+WS63\run.bat
```

只烧 WS63 默认 `.00.01` 固件：

```bat
tools\burn\VenusA+WS63\run_ws63_only.bat
```

只烧 WS63 指定 `.00.02` 固件：

```bat
tools\burn\VenusA+WS63\run_ws63_only.bat ..\ws63_fw\Midea_VenusA_WS63_35.03.01.01.18.26.05.04.00.02_20260526_200125
```

dry-run：

```bat
python tools\burn\VenusA+WS63\auto_burn.py --firmware-root tools\burn\ws63_fw\Midea_VenusA_WS63_35.03.01.01.18.26.05.04.00.01_20260519_021817 --control-port COM19 --ws63-port COM17 --ws63-verify-port COM17 --skip-venusa --dry-run
```

完整 VenusA/CSK + WS63 烧录入口为：

```bat
set VENUSA_PORT=<当前确认的VenusA或CSK烧录口>
tools\burn\VenusA+WS63\run_full_venusa_ws63.bat
```

该入口会要求输入 `YES` 二次确认，并且不再默认 `COM13`。2026-06-02 已在当前 WS63 端口组合上补做 `.00.01` VenusA/CSK + WS63 full-burn 真机验证并 PASS；后续仍必须显式确认 `VENUSA_PORT` 和烧录风险。

## 已知结论

- `.00.01` 期望 WS63 BuildInfo：`feature/mai_a1e05e5a_2026-05-19_02:18:59` 或 `feature/mai_a1e05e5a_2026-05-19_02:19:07`。
- `.00.02` 期望 WS63 BuildInfo：`feature/mai_169dcc94_2026-05-26_20:02:08` 或 `feature/mai_169dcc94_2026-05-26_20:02:16`。
- 2026-06-02 旧端口 `COM12/COM13/COM11` 下的 WS63 `.00.01`/`.00.02` 烧录记录只保留为历史/组合拓扑证据；当前 WS63 `COM20/COM17/COM19` 已补做 `.00.01` WS63-only、`.00.02` WS63-only、恢复 `.00.01` 和 VenusA/CSK + WS63 full-burn 真机验证，烧录与版本校验均 PASS。
- 2026-06-02 full-burn PASS 端口组合：VenusA/AP `COM20@3000000/921600`、WS63 BurnTool/verify `COM17@1000000/921600`、control `COM19@115200`；烧录后需恢复 `env=1/UAT`、云控基线并执行 first_wake/basic smoke。
- CSK/VenusA 全包历史上出现过 `HEX SEGMENT 2/5 FAILED`、`MD5 FORMAT ERROR`、`CONNECT ROM FAILED`，不能因为后续 WS63 fwpkg 成功而报告“全包 PASS”。
- 2026-06-02 真机补验证中，VenusA 失败阻断逻辑已验证：脚本在 `HEX SEGMENT 3/5 FAILED` 后 exit code=1，且没有生成新的 WS63 optLog，证明未继续烧 WS63。
- 同次验证也证明“人为制造 VenusA 失败”不是低风险动作：失败后旧 COM13 设备侧反复出现 `Exception on CORE0 / Illegal instruction`；后续不要在普通验证中反复触发失败烧录。
- 常规回归优先使用当前 WS63-only 自动烧录；需要覆盖 VenusA/CSK 时走 full-burn 入口并显式确认 `VENUSA_PORT`。如果 VenusA/CSK 失败，不要盲目反复全包烧录，先检查 boot/reset/串口硬件条件或使用更可靠的专用烧录夹具/流程。
