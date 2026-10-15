@echo off
set SF_ACCOUNT=OTZYIKZ-QUANDATICS_PARTNER
set SF_USER=AMMAR.BAHARUM@QUANDATICS.COM
set SF_WAREHOUSE=DEMO_WH
set SF_ROLE=ACCOUNTADMIN
set SF_CREDIT_PRICE=4.05

python C:\scripts\snowflake_reporter.py >> C:\grafana_billing_data\logs\credit_report.log 2>&1
python C:\scripts\snowflake_storage_reporter.py >> C:\grafana_billing_data\logs\storage_report.log 2>&1
