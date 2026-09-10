@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\status_app.ps1" %*
