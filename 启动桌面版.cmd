@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0dist\windows\screator\screator.exe" (
  echo Packaged screator was not found. Build it first with:
  echo node desktop\scripts\package-windows.mjs
  echo For development, run: node desktop\scripts\tauri.mjs dev
  pause
  exit /b 1
)
start "" "%~dp0dist\windows\screator\screator.exe" %*
