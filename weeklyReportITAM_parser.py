import re
import requests
import pandas as pd
from datetime import datetime
import time
import logging

# =========================
# CONFIG
# =========================
BASE_URL = "https://inventory.quandatics.com/api/v1/hardware"
API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiIxIiwianRpIjoiOWM4OGIzMjMzN2IzNDI3MGY3N2Y4YWY2YmZiNzFjMzE3NzQyODVhMWVkOTc2ODVhYmQzOWIzNWJiYmU3ZjVkOGJkMjk0MGNlMjBhNDRlYTYiLCJpYXQiOjE3NzcyODIzODIuNTI4MzU2LCJuYmYiOjE3NzcyODIzODIuNTI4MzU5LCJleHAiOjIyNTA2Njc5ODIuNTE0NDk0LCJzdWIiOiIzODciLCJzY29wZXMiOltdfQ.Ioo9ZlYjY7y9zTGq29z6OPb0fEMGGX2bdzmhsPgFZi5bAbCFzwKoJpp3dk6clkbBT_8pX2m5W4CAl7xxJxgjsvlJqn76U6LJBRJG9XqPCmP51UrzL1QK82Fzddz0oYGaU_keVC6K3mftV5aWKByhUTV4TvM0opLWhKK40GUKri9FsAQ_kMWH6SJW-ea_fdAL3OwFjkYRRSo4ma1h-SJPt1EqgiG07O_nNZ979QmpUR98tDcfcVX2YAn2zBEYo8ma5upFfcRu496uo2zjI96axHCcdJ2iYXdPOYDnEUVhpsiY9MymAAqM-105PyEJkOuGk0r5tRM93d6fo5h-RHVrcHRpyrd0Ccev0AwY5Ho-rxvxj02ada23wVbHcJcAS3uXy026xoRRE-jNhrInDxp9EkZR57RSD-124goEcsqll2YVgxpoNK2NO64Tv8yVM4cvK3BRDPwE5_Ur-yFyKst4Pv_xEOhjwSLDLz9L6cKHcIeiXnBoBJUAUJOzF1xhkH02joOKM4Xf_IesyXwprBsCTIgfK3aAKZ7maauc7L2dMA4Ka_ROOtrUZhSNYMLrLG3YWQS9mjfWk9dpsCve-aSX9SJbCYPLwWCSR3dtw4V7jYsXxMrtOyQla2iDzC_WlIxLAoV7q_0xSPWDE9-v5bg0y6uhRFmBP1NwNNdZ23Idvdg"


CSV_OUTPUT_DIR   = r"C:\scripts\output"   # same folder your http.server is serving on :8090
CSV_MAIN_NAME    = "ITAM_ParsedDATA.csv"
CSV_FAILED_NAME  = "FAILED_ROWS.csv"

INGESTION_TIME = datetime.now().strftime("%d-%b-%Y %H:%M:%S")

LIMIT = 200
MAX_RETRIES = 3
# Column A to AL (38 cols)
COLUMN_ORDER = [
    "ID", "Company", "Asset Name", "Asset Tag", "Model", "Model No.", "Serial",
    "Purchased", "Cost", "Warranty Expires", "Diff", "Fully Deprecated",
    "Order Number", "Checked Out", "Type", "Username", "Department", "Title",
    "Status", "Checkout Date", "Last Checkin Date", "Created At", "Updated At",
    "Last Audit", "Next Audit Date", "Notes", "URL", "MAC Address",
    "Full Item Name", "RAM", "Notes9",
    "Insurance Coverage Start Date", "Insurance Coverage End Date",
    "Ram2nd",             # AH - numeric RAM value
    "Age",                # AI - bucketed age from purchase date
    "Insurance Coverage", # AJ - Insured / Not Insured / Insurance Expired
    "Non-Laptop Y/N",     # AK - N if Laptop, Y if anything else
    "Status[Py]",
    "Warranty Status[Py]"         # AL - Mapped status display value
]

# =========================
# LOGGING
# =========================
logging.basicConfig(
    filename="asset_report.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logging.info("Script started")

# =========================
# SAFE HELPERS
# =========================
def safe_dict(obj):
    return obj if isinstance(obj, dict) else {}

def safe_get(obj, key, default=""):
    return safe_dict(obj).get(key, default)

def safe_cf(custom, label):
    try:
        return (custom or {}).get(label, {}).get("value", "")
    except Exception:
        return ""

def safe_float(v):
    try:
        return float(str(v).replace(",", "")) if v is not None else 0.0
    except Exception:
        return 0.0

def extract_date(obj, prefer="date"):
    if isinstance(obj, dict):
        return obj.get(prefer) or obj.get("formatted") or ""
    return obj or ""

def parse_ram(ram_str):
    """
    Strips non-numeric characters from RAM string, returns integer.
    '16GB' -> 16, '16 GB' -> 16, '' -> None
    """
    try:
        if not ram_str:
            return None
        match = re.search(r'\d+', str(ram_str))
        return int(match.group()) if match else None
    except Exception:
        return None

def calc_age_bucket(purchase_date_str):
    """
    Returns age bucket from purchase date to today.
    <1y, 1y, 2y, 3y, 4y, 5y+ - "Unidentifiable" if date missing/invalid.
    """
    try:
        if not purchase_date_str:
            return "Unidentifiable"
        pd_date = datetime.strptime(str(purchase_date_str).strip(), "%Y-%m-%d")
        years = (datetime.today() - pd_date).days / 365.25
        if years < 1:   return "<1y"
        elif years < 2: return "1y"
        elif years < 3: return "2y"
        elif years < 4: return "3y"
        elif years < 5: return "4y"
        else:           return "5y+"
    except Exception:
        return "Unidentifiable"

def calc_lifetime_bucket(purchase_date_str):
    """
    Returns lifetime bucket from purchase date to today, for the
    laptop lifetime-by-year report only.
    <=1y, 2y, 3y, 4y, >=5y - empty string if date missing/invalid.
    """
    try:
        if not purchase_date_str:
            return ""
        pd_date = datetime.strptime(str(purchase_date_str).strip(), "%Y-%m-%d")
        years = (datetime.today() - pd_date).days / 365.25
        if years <= 1:  return "<=1y"
        elif years <= 2: return "2y"
        elif years <= 3: return "3y"
        elif years <= 4: return "4y"
        else:            return ">=5y"
    except Exception:
        return ""

def calc_insurance(start_str, end_str, asset_id, asset_tag):
    """
    Returns (status, is_failed) tuple.

    Rules:
    - Both blank                              -> "Not Insured", not failed
    - Both present, today within range        -> "Insured", not failed
    - Both present, end date in past          -> "Insurance Expired", not failed
    - Both present, today before start date   -> "Not Insured", not failed
    - Only one of start/end present           -> "", failed (goes to FAILED_ROWS)
    - Date parse error                        -> "", failed (goes to FAILED_ROWS)
    """
    start_exists = bool(start_str and str(start_str).strip())
    end_exists   = bool(end_str   and str(end_str).strip())

    # Edge case: only one side present - flag as failed
    if start_exists != end_exists:
        return "", True

    # Both blank - not insured
    if not start_exists and not end_exists:
        return "Not Insured", False

    # Both present - determine status
    try:
        today    = datetime.today()
        start_dt = datetime.strptime(str(start_str).strip(), "%Y-%m-%d")
        end_dt   = datetime.strptime(str(end_str).strip(),   "%Y-%m-%d")

        if today < start_dt:
            return "Not Insured", False
        elif today <= end_dt:
            return "Insured", False
        else:
            return "Insurance Expired", False

    except Exception:
        return "", True

def is_non_laptop(asset_type):
    """
    Returns 'N' if type is Laptop, 'Y' for everything else.
    """
    try:
        return "N" if str(asset_type).strip().lower() == "laptop" else "Y"
    except Exception:
        return "Y"

def map_status(status_str):
    """
    Maps raw Status string to a simplified display value by extracting
    the keyword inside parentheses.

    | Original value               | Status[Py]  |
    | ---------------------------- | ----------- |
    | Pending (pending)            | Pending     |
    | Archived (archived)          | Archived    |
    | Ready to Deploy (deployable) | Deployable  |
    | Ready to Deploy (deployed)   | Deployed    |

    Falls back to the raw status string for any unrecognised key.
    """
    STATUS_MAP = {
        "pending":      "Pending",
        "archived":     "Archived",
        "deployable":   "Deployable",
        "deployed":     "Deployed",
    }
    try:
        match = re.search(r'\(([^)]+)\)', str(status_str))
        if match:
            key = match.group(1).strip().lower()
            return STATUS_MAP.get(key, status_str)
        return status_str
    except Exception:
        return ""

# =========================
# warrantystatus
# =========================

def calc_warranty_status(warranty_str):
    """
    Returns warranty status label.
    - "Active"         -> warranty date is more than 90 days away
    - "Expiring Soon"  -> warranty date is within 90 days
    - "Expired"        -> warranty date has passed
    - "No Warranty"    -> date is blank/missing
    """
    try:
        if not warranty_str or str(warranty_str).strip() == "":
            return "No Warranty"
        wd = datetime.strptime(str(warranty_str).strip(), "%Y-%m-%d")
        days_remaining = (wd - datetime.today()).days
        if days_remaining < 0:
            return "Expired"
        elif days_remaining <= 90:
            return "Expiring Soon"
        else:
            return "Active"
    except Exception:
        return "No Warranty"
# =========================
# API HEADERS (READ ONLY)
# =========================
headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json"
}

assets = {}
failed_rows = []

# =========================
# STATUS RESOLVER
# =========================
def resolve_status(item):
    status_label = item.get("status_label") or {}
    status_name  = status_label.get("name", "")
    status_type  = status_label.get("status_type", "")
    assigned_to  = item.get("assigned_to")

    if assigned_to:
        if status_type == "deployable":
            return f"{status_name} (deployed)"
        return f"{status_name} ({status_type})"

    return f"{status_name} ({status_type})"

# =========================
# COMPANY NORMALIZE
# =========================
def normalize_company(name):
    """
    Strips parentheses from specific company names that cause
    downstream regex-matching issues (e.g. Grafana Infinity plugin).
    'QM-QUANDATICS (M) SDN BHD' -> 'QM-QUANDATICS M SDN BHD'
    """
    try:
        if not name:
            return name
        name = str(name).strip()
        if name == "QM-QUANDATICS (M) SDN BHD":
            return "QM-QUANDATICS M SDN BHD"
        return name
    except Exception:
        return name
# =========================
# CORE FETCH FUNCTION
# =========================
def fetch_assets(scope):
    offset = 0

    while True:
        if scope == "all":
            url = f"{BASE_URL}?limit={LIMIT}&offset={offset}"
        elif scope == "archived":
            url = f"{BASE_URL}?limit={LIMIT}&offset={offset}&status=Archived"
        elif scope == "deleted":
            url = f"{BASE_URL}?limit={LIMIT}&offset={offset}&deleted=1"
        else:
            return

        for attempt in range(MAX_RETRIES):
            try:
                r = requests.get(url, headers=headers, timeout=15)
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                logging.warning(f"{scope} retry {attempt+1}: {e}")
                time.sleep(2)
        else:
            logging.error(f"{scope} failed at offset {offset}")
            return

        rows = data.get("rows", [])
        if not rows:
            break

        logging.info(f"{scope.upper()} fetched {len(rows)} records @ offset {offset}")
        print(f"  [{scope.upper()}] offset={offset} -> {len(rows)} rows fetched")

        for item in rows:
            try:
                asset_id = item.get("id")
                if asset_id in assets:
                    continue

                assigned    = item.get("assigned_to") or {}
                dept        = assigned.get("department") or {}
                custom      = item.get("custom_fields") or {}
                cost        = safe_float(item.get("purchase_cost"))
                full_status = resolve_status(item)
                warranty    = extract_date(item.get("warranty_expires"))
                purchased   = extract_date(item.get("purchase_date"))
                ram_raw     = safe_cf(custom, "RAM")
                ins_start   = safe_cf(custom, "Insurance Coverage Start Date")
                ins_end     = safe_cf(custom, "Insurance Coverage End Date")
                asset_type  = safe_get(item.get("category"), "name")

                fully_deprecated = "NO"
                try:
                    if warranty:
                        wd = datetime.strptime(warranty, "%Y-%m-%d")
                        fully_deprecated = "YES" if wd < datetime.today() else "NO"
                except Exception:
                    pass

                # Insurance Coverage logic
                ins_status, ins_failed = calc_insurance(
                    ins_start, ins_end, asset_id, item.get("asset_tag")
                )

                if ins_failed:
                    failed_rows.append({
                        "ID": asset_id,
                        "Asset Tag": item.get("asset_tag"),
                        "Scope": scope,
                        "Issue": f"Insurance date mismatch - Start: '{ins_start}', End: '{ins_end}'"
                    })
                    logging.warning(
                        f"Insurance date mismatch for asset {asset_id} "
                        f"({item.get('asset_tag')}) - Start: '{ins_start}', End: '{ins_end}'"
                    )
                    # Asset still written to main sheet with blank Insurance Coverage

                assets[asset_id] = {
                    "ID": asset_id,
                    "Company": normalize_company(safe_get(item.get("company"), "name")),
                    "Asset Name": item.get("name"),
                    "Asset Tag": item.get("asset_tag"),
                    "Model": safe_get(item.get("model"), "name"),
                    "Model No.": safe_get(item.get("model"), "model_number"),
                    "Serial": item.get("serial"),
                    "Purchased": purchased,
                    "Cost": cost,
                    "Warranty Expires": warranty,
                    "Diff": cost,
                    "Fully Deprecated": fully_deprecated,
                    "Order Number": item.get("order_number"),
                    "Checked Out": "YES" if assigned else "NO",
                    "Type": asset_type,
                    "Username": assigned.get("username", ""),
                    "Department": dept.get("name", ""),
                    "Title": assigned.get("jobtitle", ""),
                    "Status": full_status,
                    "Checkout Date": extract_date(item.get("last_checkout"), "datetime"),
                    "Last Checkin Date": extract_date(item.get("last_checkin"), "datetime"),
                    "Created At": extract_date(item.get("created_at"), "datetime"),
                    "Updated At": extract_date(item.get("updated_at"), "datetime"),
                    "Last Audit": extract_date(item.get("last_audit_date")),
                    "Next Audit Date": extract_date(item.get("next_audit_date")),
                    "Notes": item.get("notes"),
                    "URL": f"https://inventory.quandatics.com/hardware/{asset_id}",
                    "MAC Address": safe_cf(custom, "MAC Address"),
                    "Full Item Name": safe_cf(custom, "Full Item Name"),
                    "RAM": ram_raw,
                    "Notes9": safe_cf(custom, "Notes"),
                    "Insurance Coverage Start Date": ins_start,
                    "Insurance Coverage End Date": ins_end,
                    "Ram2nd": parse_ram(ram_raw),               # AH - numeric e.g. 16
                    "Age": calc_age_bucket(purchased),           # AI - e.g. "<1y", "2y", "5y+"
                    "Insurance Coverage": ins_status,            # AJ - Insured / Not Insured / Insurance Expired
                    "Non-Laptop Y/N": is_non_laptop(asset_type), # AK - N if Laptop, Y otherwise
                    "Status[Py]": map_status(full_status),
                    "Warranty Status[Py]": calc_warranty_status(warranty)     # AL - Pending / Archived / Deployable / Deployed
                }

            except Exception as e:
                logging.error(f"Row skipped [{scope}] id={item.get('id')}: {e}")
                failed_rows.append({
                    "ID": item.get("id"),
                    "Asset Tag": item.get("asset_tag"),
                    "Scope": scope,
                    "Issue": str(e)
                })

        offset += LIMIT
        time.sleep(0.5)

# =========================
# FETCH ALL SCOPES
# =========================
print("Fetching all active assets...")
fetch_assets("all")

print("Fetching archived assets...")
fetch_assets("archived")

print("Fetching deleted assets...")
fetch_assets("deleted")

print(f"\nTotal assets fetched: {len(assets)}")
logging.info(f"Total assets fetched: {len(assets)}")

# =========================
# EXPORT TO CSV
# =========================
print("Writing to CSV...")

df_main   = pd.DataFrame(list(assets.values())).reindex(columns=COLUMN_ORDER)
df_failed = pd.DataFrame(failed_rows)

import os
os.makedirs(CSV_OUTPUT_DIR, exist_ok=True)

main_path   = os.path.join(CSV_OUTPUT_DIR, CSV_MAIN_NAME)
failed_path = os.path.join(CSV_OUTPUT_DIR, CSV_FAILED_NAME)

df_main.to_csv(main_path, index=False, encoding="utf-8-sig")
# =========================
# LAPTOP RAM SUMMARY BY COMPANY (separate output - does NOT touch main CSV)
# =========================
print("Building laptop RAM summary by company...")

CSV_LAPTOP_SUMMARY_NAME = "ITAM_Laptop_RAM_Summary.csv"
laptop_summary_path = os.path.join(CSV_OUTPUT_DIR, CSV_LAPTOP_SUMMARY_NAME)

laptop_rows = [a for a in assets.values() if a.get("Non-Laptop Y/N") == "N"]
df_laptops = pd.DataFrame(laptop_rows)

if not df_laptops.empty:
    # Total laptops per company - ALL statuses count toward this
    total_per_company = (
        df_laptops.groupby("Company")["ID"].count().rename("Total Laptops")
    )

    # RAM breakdown - Deployable status only
    df_deployable = df_laptops[df_laptops["Status[Py]"] == "Deployable"].copy()
    df_deployable["RAM Label"] = df_deployable["Ram2nd"].apply(
        lambda r: f"{int(r)}GB Deployable" if pd.notna(r) else "Unknown RAM Deployable"
    )

    ram_pivot = pd.pivot_table(
        df_deployable,
        index="Company",
        columns="RAM Label",
        values="ID",
        aggfunc="count",
        fill_value=0
    )

    # Sort RAM columns numerically (8GB, 16GB, 32GB...) instead of alphabetically
    def _ram_sort_key(col):
        m = re.search(r'\d+', col)
        return int(m.group()) if m else float("inf")

    ram_cols_sorted = sorted(ram_pivot.columns, key=_ram_sort_key)
    ram_pivot = ram_pivot[ram_cols_sorted]

    summary = total_per_company.to_frame().join(ram_pivot, how="left").fillna(0)

    for col in summary.columns:
        summary[col] = summary[col].astype(int)

    summary = summary.reset_index().sort_values("Company")
else:
    summary = pd.DataFrame(columns=["Company", "Total Laptops"])

summary.to_csv(laptop_summary_path, index=False, encoding="utf-8-sig")
print(f"Laptop RAM summary written to {laptop_summary_path}")
logging.info(f"Laptop RAM summary written to {laptop_summary_path}")

# =========================
# LAPTOP LIFETIME BY YEAR, BY COMPANY (separate output)
# Reuses the "Age" bucket already computed on each asset row - no re-fetch,
# no new calc function, just pulled straight from laptop_rows.
# =========================
print("Building laptop lifetime-by-year report...")

CSV_LAPTOP_LIFETIME_NAME = "ITAM_Laptop_Lifetime_By_Year.csv"
laptop_lifetime_path = os.path.join(CSV_OUTPUT_DIR, CSV_LAPTOP_LIFETIME_NAME)

LIFETIME_BUCKET_ORDER = ["<=1y", "2y", "3y", "4y", ">=5y"]

if not df_laptops.empty:
    df_laptops = df_laptops.copy()
    df_laptops["Lifetime Bucket"] = df_laptops["Purchased"].apply(calc_lifetime_bucket)

    lifetime_pivot = pd.pivot_table(
        df_laptops,
        index="Company",
        columns="Lifetime Bucket",
        values="ID",
        aggfunc="count",
        fill_value=0
    )

    for b in LIFETIME_BUCKET_ORDER:
        if b not in lifetime_pivot.columns:
            lifetime_pivot[b] = 0
    # drop any stray/blank bucket column (e.g. "" from missing Purchased date)
    lifetime_pivot = lifetime_pivot[LIFETIME_BUCKET_ORDER]

    lifetime_pivot["Total Laptops"] = lifetime_pivot[LIFETIME_BUCKET_ORDER].sum(axis=1)
    lifetime_summary = lifetime_pivot.reset_index().sort_values("Company")
else:
    lifetime_summary = pd.DataFrame(columns=["Company"] + LIFETIME_BUCKET_ORDER + ["Total Laptops"])

lifetime_summary.to_csv(laptop_lifetime_path, index=False, encoding="utf-8-sig")
print(f"Laptop lifetime-by-year report written to {laptop_lifetime_path}")
logging.info(f"Laptop lifetime-by-year report written to {laptop_lifetime_path}")

# =========================
# SERVER & NETWORKING EQUIPMENT LIFETIME REPORT (separate output)
# =========================
print("Building server & networking equipment lifetime report...")

CSV_LIFETIME_NAME = "ITAM_Server_Network_Lifetime.csv"
lifetime_path = os.path.join(CSV_OUTPUT_DIR, CSV_LIFETIME_NAME)

def calc_lifetime_years(purchase_date_str):
    """
    Returns lifetime in years (float, 1 decimal) from purchase date to today.
    Empty string if date missing/invalid.
    """
    try:
        if not purchase_date_str:
            return ""
        pd_date = datetime.strptime(str(purchase_date_str).strip(), "%Y-%m-%d")
        years = (datetime.today() - pd_date).days / 365.25
        return round(years, 1)
    except Exception:
        return ""

SERVER_NETWORK_KEYWORDS = ("server", "network")

server_network_rows = []
for a in assets.values():
    asset_type = str(a.get("Type") or "").lower()
    if any(kw in asset_type for kw in SERVER_NETWORK_KEYWORDS):
        server_network_rows.append({
            "ID": a.get("ID"),
            "Company": a.get("Company"),
            "Asset Name": a.get("Asset Name"),
            "Asset Tag": a.get("Asset Tag"),
            "Type": a.get("Type"),
            "Model": a.get("Model"),
            "Serial": a.get("Serial"),
            "Purchased": a.get("Purchased"),
            "Lifetime (Years)": calc_lifetime_years(a.get("Purchased")),
            "Age": a.get("Age"),
            "Warranty Expires": a.get("Warranty Expires"),
            "Warranty Status[Py]": a.get("Warranty Status[Py]"),
            "Status": a.get("Status"),
        })

df_lifetime = pd.DataFrame(server_network_rows)
if not df_lifetime.empty:
    df_lifetime = df_lifetime.sort_values(["Company", "Type", "Purchased"])

df_lifetime.to_csv(lifetime_path, index=False, encoding="utf-8-sig")
print(f"Server & networking lifetime report written to {lifetime_path}")
logging.info(f"Server & networking lifetime report written to {lifetime_path}")

df_failed.to_csv(failed_path, index=False, encoding="utf-8-sig")

logging.info("Script finished")
print(f"Done! CSV updated at {main_path}")