import boto3
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

# ---------- CONFIG ----------
BUCKET = "grafana-aws-cost-exports"
PREFIX = "cur/grafana-cost-export/data/"
PROFILE = "cost-export-reader"
LOCAL_DOWNLOAD_DIR = Path("./cur_downloads")
OUTPUT_DIR = Path(r"C:\scripts\output")

LOCAL_DOWNLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# ---------- STEP 1: connect to S3 ----------
session = boto3.Session(profile_name=PROFILE)
s3 = session.client("s3")

# ---------- STEP 2: find the latest file(s) under the prefix ----------
def list_latest_objects(bucket, prefix):
    paginator = s3.get_paginator("list_objects_v2")
    objects = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objects.extend(page.get("Contents", []))
    if not objects:
        raise RuntimeError(f"No objects found under s3://{bucket}/{prefix}")
    objects.sort(key=lambda o: o["LastModified"], reverse=True)
    return objects

objects = list_latest_objects(BUCKET, PREFIX)
latest_key = objects[0]["Key"]
latest_billing_period = latest_key.split("BILLING_PERIOD=")[1].split("/")[0]
print(f"Latest billing period found: {latest_billing_period}")

current_period_files = [
    o["Key"] for o in objects
    if f"BILLING_PERIOD={latest_billing_period}" in o["Key"] and o["Key"].endswith(".parquet")
]
print(f"Found {len(current_period_files)} parquet file(s) for this period")

# ---------- STEP 3: download them ----------
local_files = []
for key in current_period_files:
    local_path = LOCAL_DOWNLOAD_DIR / Path(key).name
    s3.download_file(BUCKET, key, str(local_path))
    local_files.append(local_path)
    print(f"Downloaded {key}")

# ---------- STEP 4: load into pandas ----------
df = pd.concat([pd.read_parquet(f) for f in local_files], ignore_index=True)
print(f"Loaded {len(df)} rows")

COL_DATE = "line_item_usage_start_date"
COL_SERVICE = "line_item_product_code"
COL_COST = "line_item_unblended_cost"
COL_LINE_TYPE = "line_item_line_item_type"
COL_FAMILY = "product_product_family"

df[COL_DATE] = pd.to_datetime(df[COL_DATE])
df[COL_COST] = df[COL_COST].astype(float)

# ---------- STEP 5: filter to charges that should count toward spend ----------
# Usage / DiscountedUsage = normal metered AWS usage
# FlatRateSubscription = flat-fee Marketplace subscriptions (e.g. Kiro)
# NOT included: Tax, Credit, BundledDiscount — decide later if Credit should net off the total
KEEP_LINE_TYPES = ['Usage', 'DiscountedUsage', 'FlatRateSubscription']
df = df[df[COL_LINE_TYPE].isin(KEEP_LINE_TYPES)]
print(f"After filtering to {KEEP_LINE_TYPES}: {len(df)} rows remaining")

# ---------- STEP 6: build display names ----------
service_mappings = {
    'AmazonEC2': 'EC2',   # overridden below by family split
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
    'AmazonRDS': 'RDS',

    # Marketplace items — hardcoded since these change rarely.
    # New unfamiliar codes get flagged automatically by the check at the bottom of this script.
    '5jl6d7hjcj7sd8pjt4cag3ttm0': 'Claude Haiku 4.5 (Amazon Bedrock Edition)',
    '7g37zhparap7eesm9k78jrzqc': 'Claude 3.5 Sonnet v2 (Amazon Bedrock Edition)',
    # Kiro's product code goes here once confirmed — see diagnostic print below
}

def get_display_name(row):
    code = row[COL_SERVICE]
    family = row.get(COL_FAMILY, '')

    if code == 'AmazonEC2':
        return 'Amazon Elastic Compute Cloud - Compute' if family == 'Compute' else 'EC2 - Other'

    return service_mappings.get(code, code)  # unmapped codes pass through as-is, caught below

df[COL_SERVICE] = df.apply(get_display_name, axis=1)

DECIMALS = 6
df[COL_COST] = df[COL_COST].round(DECIMALS)

# ---------- STEP 7: build the breakdowns ----------

total_spend = df[COL_COST].sum().round(DECIMALS)
pd.DataFrame([{"total_spend_to_date": total_spend}]).to_csv(
    OUTPUT_DIR / "aws_total_spend.csv", index=False, float_format='%.6f'
)

top5_alltime = (
    df.groupby(COL_SERVICE)[COL_COST].sum()
    .sort_values(ascending=False).head(5)
    .reset_index().rename(columns={COL_SERVICE: "service", COL_COST: "cost"})
)
top5_alltime['cost'] = top5_alltime['cost'].round(DECIMALS)
top5_alltime.to_csv(OUTPUT_DIR / "aws_top5_alltime.csv", index=False, float_format='%.6f')

today = datetime.now(timezone.utc)
mtd_df = df[(df[COL_DATE].dt.year == today.year) & (df[COL_DATE].dt.month == today.month)]
top5_mtd = (
    mtd_df.groupby(COL_SERVICE)[COL_COST].sum()
    .sort_values(ascending=False).head(5)
    .reset_index().rename(columns={COL_SERVICE: "service", COL_COST: "cost"})
)
top5_mtd['cost'] = top5_mtd['cost'].round(DECIMALS)
top5_mtd.to_csv(OUTPUT_DIR / "aws_top5_mtd.csv", index=False, float_format='%.6f')

daily_breakdown = (
    df.groupby([df[COL_DATE].dt.date, COL_SERVICE])[COL_COST]
    .sum().reset_index()
    .rename(columns={COL_DATE: "date", COL_SERVICE: "service", COL_COST: "cost"})
)
daily_breakdown['cost'] = daily_breakdown['cost'].round(DECIMALS)
daily_breakdown.to_csv(OUTPUT_DIR / "aws_daily_breakdown.csv", index=False, float_format='%.6f')

print("Done. CSVs written to", OUTPUT_DIR.resolve())
print(f"Total spend: ${total_spend:.2f}")

# ---------- STEP 8: flag anything still unmapped (raw AWS codes slipping through) ----------
# Anything here is either a raw code like "AmazonEC2" you forgot to map, or a new
# Marketplace product code you haven't identified yet — check its cost, then either
# add it to service_mappings above, or look it up in AWS Marketplace > Manage subscriptions.
known_raw_codes = set(service_mappings.keys()) | {'Amazon Elastic Compute Cloud - Compute', 'EC2 - Other'}
unmapped = df[~df[COL_SERVICE].isin(known_raw_codes)]
if not unmapped.empty:
    print("\n--- UNMAPPED codes found (check these) ---")
    print(unmapped.groupby(COL_SERVICE)[COL_COST].sum().sort_values(ascending=False).to_string())
else:
    print("\nNo unmapped codes this run.")