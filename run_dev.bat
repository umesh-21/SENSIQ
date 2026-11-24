@echo off
rem Run PII Guard from source after build.ps1 has created .venv (shows a log in this window).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m piiguard --debug %*
