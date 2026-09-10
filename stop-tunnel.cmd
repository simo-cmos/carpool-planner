@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\stop_tunnel.ps1" %*
