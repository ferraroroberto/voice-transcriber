@echo off
chcp 65001 >nul
REM Stream Deck — quick English transcription (no translation).
call "%~dp0quick_record.bat" --language english
