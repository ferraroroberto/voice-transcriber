@echo off
chcp 65001 >nul
REM Stream Deck — quick Spanish → English translation.
call "%~dp0quick_record.bat" --language spanish-to-english
