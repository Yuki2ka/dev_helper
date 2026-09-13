@echo off
set PYTHONDONTWRITEBYTECODE=1
python -m pytest -p no:cacheprovider to_clipboard_test.py -v
pause
