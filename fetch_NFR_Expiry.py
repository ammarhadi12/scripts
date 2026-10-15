import pandas as pd
from datetime import datetime

# Path where you want your tracker CSV to be saved
# (Using absolute paths is highly recommended for Task Scheduler!)
CSV_PATH = r"C:\scripts\output\NFR_LicensesExpiry.csv"

# 1. Define your core asset data
data = [
    {"Asset Name": "Altair", "Type": "NFR License", "Expiration Date": "2026-11-30", "Remarks": ""},
    {"Asset Name": "Quandatics Academy Domain", "Type": "Domain", "Expiration Date": "2026-11-25", "Remarks": ""},
    {"Asset Name": "Talend on-prem", "Type": "NFR License", "Expiration Date": "2027-02-17", "Remarks": ""},
    {"Asset Name": "Talend cloud", "Type": "NFR License", "Expiration Date": "2027-02-25", "Remarks": ""},
    {"Asset Name": "TDC", "Type": "NFR License", "Expiration Date": "2027-04-01", "Remarks": ""},
    {"Asset Name": "Tableau", "Type": "NFR License", "Expiration Date": "2027-04-18", "Remarks": "Should be auto renewal"},
    {"Asset Name": "Qlik", "Type": "NFR License", "Expiration Date": "2027-05-11", "Remarks": ""},
    {"Asset Name": "Quandatics Domain", "Type": "Domain", "Expiration Date": "2027-10-07", "Remarks": ""},
]

# 2. Convert to DataFrame
df = pd.DataFrame(data)

# 3. Calculate "Days Until Expiry" as static integers
today = datetime.now().date()
days_until = []

for date_str in df["Expiration Date"]:
    exp_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    delta = (exp_date - today).days
    days_until.append(delta)

# Insert the calculated values into the DataFrame
df.insert(3, "Days Until Expiry", days_until)

# 4. Save to CSV (overwrites the previous file with updated values)
df.to_csv(CSV_PATH, index=False)
print(f"[{datetime.now()}] CSV successfully updated!")