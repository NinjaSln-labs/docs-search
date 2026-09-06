@echo off
chcp 65001 >nul 2>&1
python "%~dp0docs-search.py" %*
