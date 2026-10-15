import pandas as pd
from pathlib import Path
from datetime import datetime

# ---------- CONFIG ----------
INPUT_DIR = Path(r"C:\scripts\output")
OUTPUT_DIR = Path(r"C:\scripts\output")
DAILY_FILE = INPUT_DIR / "aws_daily_breakdown.csv"
MANUAL_FILE = INPUT_DIR / "aws_manual_historical.csv"
ARCHIVE_FILE = OUTPUT_DIR / "aws_monthly_archive.csv"      # NEW: permanent, never truncated
MONTHLY_OUTPUT = OUTPUT_DIR / "aws_monthly_breakdown.csv"    # rolling 3-month view for Grafana

service_mappings = {
    'AmazonEC2': 'EC2',
    'AmazonS3': 'S3',
    'AmazonVPC': 'VPC',
    'AmazonCloudWatch': 'CloudWatch',
    'AmazonSES': 'SES',
    'AmazonSNS': 'SNS',
    'AWSGlue': 'Glue',
    'AWSIoT': 'IoT',
    'AWSLambda': 'Lambda',
    'AWSDataTransfer': 'Data Transfer',
    'AmazonCloudFront': 'CloudFront',
    'AmazonEFS': 'EFS',
    'AmazonQuickSight': 'QuickSight',
    'AWSEvents': 'EventBridge',
    'AWSGreengrass': 'Greengrass',
    'AmazonBedrockAgentCore': 'Bedrock AgentCore',
    'awskms': 'KMS',
    '5jl6d7hjcj7sd8pjt4cag3ttm0': 'Claude Haiku 4.5 (Amazon Bedrock Edition)',
    '7g37zhparap7eesm9k78jrzqc': 'Claude 3.5 Sonnet v2 (Amazon Bedrock Edition)',
    'AmazonRDS': 'RDS',
}

manual_column_mappings = {
    'APN Annual Program Fee($)': 'APN Annual Program Fee',
    'Tax($)': 'Tax',
    'Relational Database Service($)': 'RDS',
    'Claude Haiku 4.5 ( Bedrock Edition)($)': 'Claude Haiku 4.5 (Amazon Bedrock Edition)',
    'EC2-Other($)': 'EC2 - Other',
    'VPC($)': 'VPC',
    'Bedrock AgentCore($)': 'Bedrock AgentCore',
    'WAF($)': 'WAF',
    'Key Management Service($)': 'KMS',
    'CloudWatch($)': 'CloudWatch',
    'Claude Sonnet 4.5 ( Bedrock Edition)($)': 'Claude 3.5 Sonnet v2 (Amazon Bedrock Edition)',
    'S3($)': 'S3',
    'SES($)': 'SES',
    'Bedrock($)': 'Bedrock AgentCore',
    'EC2 Container Registry (ECR)($)': 'ECR',
    'API Gateway($)': 'API Gateway',
    'Secrets Manager($)': 'Secrets Manager',
    'Elastic File System($)': 'EFS',
    'CloudFormation($)': 'CloudFormation',
    'Glue($)': 'Glue',
    'Lambda($)': 'Lambda',
    'X-Ray($)': 'X-Ray',
    'Cognito($)': 'Cognito',
    'SNS($)': 'SNS',
    'SQS($)': 'SQS',
    'CodeBuild($)': 'CodeBuild',
    'Total costs($)': 'Total costs($)',
}

month_order = {
    'January': 1, 'February': 2, 'March': 3, 'April': 4,
    'May': 5, 'June': 6, 'July': 7, 'August': 8,
    'September': 9, 'October': 10, 'November': 11, 'December': 12
}

def month_key(x):
    parts = x.split(' ')
    return int(parts[1]) * 12 + month_order[parts[0]]

# ---------- STEP 1: load the daily breakdown (current month only) ----------
print(f"Loading daily breakdown from: {DAILY_FILE}")
df_daily = pd.read_csv(DAILY_FILE)
df_daily['date'] = pd.to_datetime(df_daily['date'])
df_daily['service'] = df_daily['service'].map(service_mappings).fillna(df_daily['service'])
df_daily['year_month'] = df_daily['date'].dt.strftime('%B %Y')

current_month_pivot = (
    df_daily.groupby(['year_month', 'service'])['cost'].sum()
    .reset_index()
    .pivot(index='year_month', columns='service', values='cost')
    .fillna(0)
)
print(f"Current month pivot covers: {current_month_pivot.index.tolist()}")

# ---------- STEP 2: load manual historical baseline (one-time seed, e.g. pre-CUR months) ----------
manual_df = pd.read_csv(MANUAL_FILE)
manual_df = manual_df[~manual_df['Month'].str.contains('Service total', na=False)]
manual_df = manual_df.rename(columns=manual_column_mappings)
manual_df = manual_df.set_index('Month')
manual_df.index.name = 'year_month'
manual_df = manual_df.T.groupby(level=0).sum(numeric_only=True).T  # merge duplicate-named columns after mapping

# ---------- STEP 3: load the PERSISTENT archive (every month this script has ever processed) ----------
if ARCHIVE_FILE.exists():
    archive_df = pd.read_csv(ARCHIVE_FILE, index_col='year_month')
    print(f"Loaded archive with months: {archive_df.index.tolist()}")
else:
    archive_df = pd.DataFrame()
    print("No archive yet — starting fresh")

# ---------- STEP 4: merge everything — manual (oldest priority) -> archive -> current month (freshest wins) ----------
# concat in priority order, then keep the LAST occurrence of each month
combined = pd.concat([manual_df, archive_df, current_month_pivot])
combined = combined.groupby(level=0).last()
combined = combined.fillna(0)

# ---------- STEP 5: recompute total column cleanly ----------
if 'Total costs($)' in combined.columns:
    combined = combined.drop(columns=['Total costs($)'])
combined['Total costs($)'] = combined.drop(columns=[c for c in combined.columns if c == 'Total costs($)']).sum(axis=1)

# ---------- STEP 6: sort chronologically ----------
combined['sort_order'] = combined.index.map(month_key)
combined = combined.sort_values('sort_order').drop('sort_order', axis=1)
combined = combined.round(6)

# ---------- STEP 7: save the FULL archive permanently — this fixes the root bug ----------
combined.to_csv(ARCHIVE_FILE, float_format='%.6f')
print(f"Archive updated: {ARCHIVE_FILE} — now covers {combined.index.tolist()}")

# ---------- STEP 8: build the rolling 3-month view for Grafana, FROM the archive ----------
current_month_str = datetime.now().strftime('%B %Y')
current_key = month_key(current_month_str)
cutoff_key = current_key - 2

rolling_df = combined[
    combined.index.map(lambda x: cutoff_key <= month_key(x) <= current_key)
]
rolling_df.to_csv(MONTHLY_OUTPUT, float_format='%.6f')

print(f"\nMonthly breakdown (rolling 3 months) saved to: {MONTHLY_OUTPUT}")
print(rolling_df.to_string())

print("\n=== Current month total ===")
if current_month_str in rolling_df.index:
    print(f"{current_month_str}: ${rolling_df.loc[current_month_str, 'Total costs($)']:.2f}")
else:
    print(f"No data yet for {current_month_str}")

# ---------- STEP 9: build all-time total per service (summed across every archived month) ----------
ALLTIME_SERVICE_OUTPUT = OUTPUT_DIR / "aws_alltime_by_service.csv"

service_cols = [c for c in combined.columns if c != 'Total costs($)']
alltime_totals = combined[service_cols].sum(axis=0).sort_values(ascending=False)

alltime_df = alltime_totals.reset_index()
alltime_df.columns = ['service', 'total_cost']
alltime_df['total_cost'] = alltime_df['total_cost'].round(6)

alltime_df.to_csv(ALLTIME_SERVICE_OUTPUT, index=False, float_format='%.6f')
print(f"\nAll-time by-service totals saved to: {ALLTIME_SERVICE_OUTPUT}")
print(alltime_df.to_string(index=False))