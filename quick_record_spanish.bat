@echo off
chcp 65001 >nul
REM Stream Deck — quick Spanish dictation.
call "%~dp0quick_record.bat" --language spanish
