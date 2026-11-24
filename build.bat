@echo off
rem Double-click to build PII Guard (needs internet once, for packages and the model).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
pause
