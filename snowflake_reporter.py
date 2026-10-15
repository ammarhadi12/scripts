import os
import sys
import csv
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
import snowflake.connector
from datetime import datetime
from dateutil.relativedelta import relativedelta
from decimal import Decimal

OUTPUT_DIR = r"C:\scripts\output"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "snowflake_billing.csv")
CSV_HEADERS = ["MONTH", "WAREHOUSE_NAME", "COMPUTE_CREDITS", "CLOUD_SERVICE_CREDITS", "TOTAL_CREDITS", "ESTIMATED_COST_USD"]


def month_sort_key(month_str):
    """Parse 'YYYY-MM' for chronological sorting. Unparseable values sort first."""
    try:
        return datetime.strptime(month_str, "%Y-%m")
    except (ValueError, TypeError):
        return datetime.min


def main():
    private_key_path = os.environ.get("SF_PRIVATE_KEY_PATH", r"C:\scripts\sf_private_key.p8")
    account      = os.environ.get("SF_ACCOUNT")
    user         = os.environ.get("SF_USER")
    warehouse    = os.environ.get("SF_WAREHOUSE", "DEMO_WH")
    role         = os.environ.get("SF_ROLE", "ACCOUNTADMIN")
    credit_price = float(os.environ.get("SF_CREDIT_PRICE", 4.05))

    if not os.path.exists(private_key_path):
        print("ERROR: Private key file not found at " + private_key_path)
        sys.exit(1)
    if not account:
        print("ERROR: SF_ACCOUNT not set"); sys.exit(1)
    if not user:
        print("ERROR: SF_USER not set"); sys.exit(1)

    with open(private_key_path, 'r') as f:
        private_key_raw = f.read()

    print(f"Connecting with: user={user}, role={role}, warehouse={warehouse}")

    try:
        p_key = serialization.load_pem_private_key(
            private_key_raw.encode(),
            password=None,
            backend=default_backend()
        )

        conn = snowflake.connector.connect(
            account=account,
            user=user,
            private_key=p_key,
            warehouse=warehouse,
            role=role
        )

        cursor = conn.cursor()

        query = """
            SELECT
                warehouse_name,
                ROUND(SUM(credits_used_compute), 2)        AS compute_credits,
                ROUND(SUM(credits_used_cloud_services), 2) AS cloud_service_credits,
                ROUND(SUM(credits_used), 2)                AS total_credits,
                ROUND(SUM(credits_used) * %s, 2)           AS estimated_cost_usd
            FROM snowflake.account_usage.warehouse_metering_history
            WHERE start_time >= DATE_TRUNC('month', DATEADD(month, -1, CURRENT_DATE()))
              AND start_time <  DATE_TRUNC('month', CURRENT_DATE())
            GROUP BY warehouse_name
            HAVING SUM(credits_used) > 0.1
            ORDER BY total_credits DESC;
        """

        cursor.execute(query, (credit_price,))
        results = cursor.fetchall()

        report_month = (datetime.now() - relativedelta(months=1)).strftime("%Y-%m")

        converted_results = []
        total_credits = 0.0
        total_cost    = 0.0

        for row in results:
            warehouse_name        = row[0]
            compute_credits       = float(row[1]) if isinstance(row[1], Decimal) else (row[1] or 0.0)
            cloud_service_credits = float(row[2]) if isinstance(row[2], Decimal) else (row[2] or 0.0)
            total_credits_val     = float(row[3]) if isinstance(row[3], Decimal) else (row[3] or 0.0)
            cost_val              = float(row[4]) if isinstance(row[4], Decimal) else (row[4] or 0.0)

            converted_results.append([
                report_month,
                warehouse_name,
                compute_credits,
                cloud_service_credits,
                total_credits_val,
                cost_val
            ])

            total_credits += total_credits_val
            total_cost    += cost_val
        save_to_csv(converted_results, report_month)

        print(f"\n📊 CREDIT REPORT FOR {report_month}")
        print("-" * 80)
        for row in converted_results:
            print(f"  {row[1]} | {row[4]} credits | ${row[5]}")
        print(f"SUMMARY: {round(total_credits, 2)} credits = ${round(total_cost, 2)}")
        print("=" * 80)

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"ERROR: {str(e)}")
        sys.exit(1)


def save_to_csv(new_rows, report_month):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    existing_rows = []
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, newline='', encoding='utf-8') as f:
            rows = list(csv.reader(f))
            if rows:
                existing_rows = [r for r in rows[1:] if r and r[0] != report_month]
        print(f"📂 Existing file found — keeping {len(existing_rows)} rows, replacing {report_month} data")
    else:
        print(f"📂 No existing file — creating {OUTPUT_FILE}")

    all_rows = existing_rows + new_rows
    all_rows.sort(key=lambda r: month_sort_key(r[0]))

    with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        writer.writerows(all_rows)

    print(f"\n✅ Saved to {OUTPUT_FILE}")
    print(f"   Rows written: {len(new_rows)} | Month: {report_month}")


if __name__ == "__main__":
    main()