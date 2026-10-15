@echo off
cd /d C:\scripts

echo ===================================== >> C:\scripts\scheduler\logs\run_log.txt
echo Run started: %date% %time% >> C:\scripts\scheduler\logs\run_log.txt

echo --- Running daily CUR export --- >> C:\scripts\scheduler\logs\run_log.txt
python C:\scripts\aws_cost_export_daily.py >> C:\scripts\scheduler\logs\run_log.txt 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Daily export failed with code %ERRORLEVEL%, skipping aggregator >> C:\scripts\scheduler\logs\run_log.txt
    goto :end
)

echo --- Running monthly aggregator --- >> C:\scripts\scheduler\logs\run_log.txt
python C:\scripts\aws_monthly_aggregator.py >> C:\scripts\scheduler\logs\run_log.txt 2>&1

:end
echo Run finished: %date% %time% >> C:\scripts\scheduler\logs\run_log.txt
echo. >> C:\scripts\scheduler\logs\run_log.txt