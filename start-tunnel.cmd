@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\start_tunnel.ps1" %*
