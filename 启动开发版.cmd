@echo off
setlocal
cd /d "%~dp0desktop"
node scripts\tauri.mjs dev
if errorlevel 1 pause
