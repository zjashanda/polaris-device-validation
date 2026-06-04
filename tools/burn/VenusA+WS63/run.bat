@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "FIRMWARE_ROOT=%SCRIPT_DIR%..\ws63_fw\Midea_VenusA_WS63_35.03.01.01.18.26.05.04.00.01_20260519_021817"

rem Current WS63 bench profile. Do not fall back to legacy WB01/combo COM12/COM13/COM11.
if "%CONTROL_PORT%"=="" set "CONTROL_PORT=COM19"
if "%WS63_BURN_PORT%"=="" set "WS63_BURN_PORT=COM17"
if "%WS63_VERIFY_PORT%"=="" set "WS63_VERIFY_PORT=%WS63_BURN_PORT%"

echo WS63 AP/profile port: COM20 @ 921600
echo WS63 upper/asr burn/log port: %WS63_BURN_PORT%
echo WS63 control port: %CONTROL_PORT%

rem Default to WS63-only. VenusA/CSK full burn has no safe current default port.
python "%SCRIPT_DIR%auto_burn.py" --firmware-root "%FIRMWARE_ROOT%" --control-port %CONTROL_PORT% --ws63-port %WS63_BURN_PORT% --ws63-verify-port %WS63_VERIFY_PORT% --skip-venusa
pause
