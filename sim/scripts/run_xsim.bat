@echo off
rem Thin wrapper: the real work is in run_xsim.ps1 (see the comment there).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_xsim.ps1" %*
