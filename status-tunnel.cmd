@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\status_tunnel.ps1" %*
