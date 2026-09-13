@echo off
REM minimal mirror: skips .git and benchmark_results plus build artifacts
pushd "%~dp0" >nul 2>&1
python "%~dp0backup.py" --mode mirror %*
popd
