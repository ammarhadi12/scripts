import os
import sys
import csv
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
import snowflake.connector
from datetime import datetime
from dateutil.relativedelta import relativedelta
from decimal import Decimal

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUTPUT_DIR = r"C:\scripts\output"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "snowflake_storage.csv")
CSV_HEADERS = ["MONTH", "AVG_STORAGE_TB", "AVG_FAILSAFE_TB", "AVG_STAGE_TB",
               "STORAGE_COST_USD", "FAILSAFE_COST_USD", "STAGE_COST_USD", "TOTAL_STORAGE_COST_USD"]

STORAGE_RATE_PER_TB = 23.0


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
        print(f"ERROR: Private key file not found at {private_key_path}")
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
                ROUND(AVG(storage_bytes)  / POWER(1024,4), 6) AS avg_storage_tb,
                ROUND(AVG(failsafe_bytes) / POWER(1024,4), 6) AS avg_failsafe_tb,
                ROUND(AVG(stage_bytes)    / POWER(1024,4), 6) AS avg_stage_tb,

                ROUND(AVG(storage_bytes)  / POWER(1024,4) * %s, 4) AS storage_cost_usd,
                ROUND(AVG(failsafe_bytes) / POWER(1024,4) * %s, 4) AS failsafe_cost_usd,
                ROUND(AVG(stage_bytes)    / POWER(1024,4) * %s, 4) AS stage_cost_usd,

                ROUND((AVG(storage_bytes) + AVG(failsafe_bytes) + AVG(stage_bytes)) / POWER(1024,4) * %s, 4) AS total_storage_cost_usd

            FROM snowflake.account_usage.storage_usage
            WHERE usage_date >= DATE_TRUNC('month', DATEADD(month, -1, CURRENT_DATE()))
              AND usage_date <  DATE_TRUNC('month', CURRENT_DATE());
        """

        cursor.execute(query, (STORAGE_RATE_PER_TB,) * 4)
        row = cursor.fetchone()

        report_month    = (datetime.now().replace(day=1) - relativedelta(months=1)).strftime("%Y-%m")
        avg_storage_tb  = float(row[0]) if isinstance(row[0], Decimal) else (row[0] or 0.0)
        avg_failsafe_tb = float(row[1]) if isinstance(row[1], Decimal) else (row[1] or 0.0)
        avg_stage_tb    = float(row[2]) if isinstance(row[2], Decimal) else (row[2] or 0.0)
        storage_cost    = float(row[3]) if isinstance(row[3], Decimal) else (row[3] or 0.0)
        failsafe_cost   = float(row[4]) if isinstance(row[4], Decimal) else (row[4] or 0.0)
        stage_cost      = float(row[5]) if isinstance(row[5], Decimal) else (row[5] or 0.0)
        total_cost      = float(row[6]) if isinstance(row[6], Decimal) else (row[6] or 0.0)

        print(f"\n STORAGE REPORT FOR {report_month}")
        print("-" * 80)
        print(f"  Active Storage : {avg_storage_tb} TB  -> ${storage_cost}")
        print(f"  Failsafe       : {avg_failsafe_tb} TB  -> ${failsafe_cost}")
        print(f"  Stage          : {avg_stage_tb} TB  -> ${stage_cost}")
        print(f"  TOTAL          :                    ${total_cost}")
        print("=" * 80)
        print("\nPushing to Google Sheets...")
        print("Saved to " + OUTPUT_FILE)

        save_to_csv(report_month, avg_storage_tb, avg_failsafe_tb, avg_stage_tb,
                    storage_cost, failsafe_cost, stage_cost, total_cost)

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"ERROR: {str(e)}")
        sys.exit(1)


def save_to_csv(report_month, avg_storage_tb, avg_failsafe_tb, avg_stage_tb,
                storage_cost, failsafe_cost, stage_cost, total_cost):
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

    new_row = [report_month, avg_storage_tb, avg_failsafe_tb, avg_stage_tb,
               storage_cost, failsafe_cost, stage_cost, total_cost]
    all_rows = existing_rows + [new_row]
    all_rows.sort(key=lambda r: month_sort_key(r[0]))

    with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        writer.writerows(all_rows)

    print(f"\n✅ Saved to {OUTPUT_FILE}")
    print(f"   Month: {report_month} | Total cost: ${total_cost}")


if __name__ == "__main__":
    main()