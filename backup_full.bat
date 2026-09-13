@echo off
REM archive backup: includes .git and benchmark_results, skips build artifacts
pushd "%~dp0" >nul 2>&1
python "%~dp0backup.py" --mode full %*
popd
