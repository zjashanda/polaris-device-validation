@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "FIRMWARE_ROOT=%SCRIPT_DIR%..\ws63_fw\Midea_VenusA_WS63_35.03.01.01.18.26.05.04.00.01_20260519_021817"

rem Current WS63 bench profile. VenusA/CSK burn port must be supplied explicitly.
if "%CONTROL_PORT%"=="" set "CONTROL_PORT=COM19"
if "%WS63_BURN_PORT%"=="" set "WS63_BURN_PORT=COM17"
if "%WS63_VERIFY_PORT%"=="" set "WS63_VERIFY_PORT=%WS63_BURN_PORT%"

if "%VENUSA_PORT%"=="" (
  echo ERROR: No current VenusA/CSK burn port is configured.
  echo Set VENUSA_PORT explicitly only after confirming the hardware topology.
  echo Legacy COM13 belongs to the old WB01/combo topology and is not a safe default for current WS63.
  pause
  exit /b 1
)

echo WARNING: VenusA/CSK full burn is not validated as PASS on this bench.
echo It has produced HEX/MD5/CONNECT failures and can leave VenusA unhealthy.
echo WS63 AP/profile port: COM20 @ 921600
echo WS63 upper/asr burn/log port: %WS63_BURN_PORT%
echo WS63 control port: %CONTROL_PORT%
echo VenusA/CSK burn port: %VENUSA_PORT%
set /p CONFIRM=Type YES to continue full VenusA+WS63 burn:
if not "%CONFIRM%"=="YES" (
  echo Cancelled.
  pause
  exit /b 1
)

python "%SCRIPT_DIR%auto_burn.py" --firmware-root "%FIRMWARE_ROOT%" --control-port %CONTROL_PORT% --venusa-port %VENUSA_PORT% --ws63-port %WS63_BURN_PORT% --ws63-verify-port %WS63_VERIFY_PORT%
pause
