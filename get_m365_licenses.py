import requests
import os
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

# ==========================================
# CONFIGURATION - Add your Azure App details
# ==========================================
TENANT_ID = "8af4cff3-6ddb-47dc-95e3-1ef13b51a21a"
CLIENT_ID = "ab39c775-945f-4fc1-9eab-9f6de262cd21"
CLIENT_SECRET = "pVt8Q~nmKpvMTBkZs3dbUN14zG1D5~hbE5Yzadsf"
PUSHGATEWAY   = "localhost:9091"


def get_access_token():
    token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    token_data = {
        "grant_type":    "client_credentials",
        "scope":         "https://graph.microsoft.com/.default",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    res = requests.post(token_url, data=token_data)
    res.raise_for_status()
    return res.json()["access_token"]


def get_license_data(access_token):
    url     = "https://graph.microsoft.com/v1.0/subscribedSkus"
    headers = {"Authorization": f"Bearer {access_token}"}
    res     = requests.get(url, headers=headers)
    res.raise_for_status()
    return res.json().get("value", [])


def push_to_prometheus(license_data):
    registry   = CollectorRegistry()

    g_total    = Gauge("m365_licenses_total",    "Total licensed seats",    ["sku"], registry=registry)
    g_assigned = Gauge("m365_licenses_assigned", "Assigned licensed seats", ["sku"], registry=registry)
    g_free     = Gauge("m365_licenses_free",     "Free licensed seats",     ["sku"], registry=registry)

    for sku in license_data:
        sku_name = sku["skuPartNumber"]
        total    = sku["prepaidUnits"]["enabled"]
        assigned = sku["consumedUnits"]
        free     = total - assigned
        g_total.labels(sku=sku_name).set(total)
        g_assigned.labels(sku=sku_name).set(assigned)
        g_free.labels(sku=sku_name).set(free)

        print(f"  {sku_name}: total={total}, assigned={assigned}, free={free}")

    push_to_gateway(PUSHGATEWAY, job="m365_licenses", registry=registry)
    print("\n✓ Successfully pushed to Prometheus Pushgateway!")


def main():
    print("Fetching M365 license data...")
    token        = get_access_token()
    license_data = get_license_data(token)
    print(f"Found {len(license_data)} SKUs:\n")
    push_to_prometheus(license_data)


if __name__ == "__main__":
    main()