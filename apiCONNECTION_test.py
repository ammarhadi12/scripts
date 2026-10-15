import requests
import urllib3

# Suppress self-signed SSL warnings if Proxmox uses a default cert
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROXMOX_IP = "10.1.10.2"  # Replace with your Proxmox VE IP
TOKEN_ID = "grafana_integ@pve!monitoring-read"
TOKEN_SECRET = "e37f6b11-078f-41c1-b88f-b2739cad63dc"  # The UUID key shown when created

headers = {
    "Authorization": f"PVEAPIToken={TOKEN_ID}={TOKEN_SECRET}"
}

url = f"https://{PROXMOX_IP}:8006/api2/json/nodes/proxmox/tasks?limit=50&typefilter=vzdump"

try:
    response = requests.get(url, headers=headers, verify=False, timeout=10)
    response.raise_for_status()
    tasks = response.json().get('data', [])
    
    print(f"Successfully connected! Retrieved {len(tasks)} vzdump backup tasks.\n")
    if tasks:
        sample = tasks[0]
        print("Sample Backup Task Payload:")
        print(f" - VM/Container ID : {sample.get('id')}")
        print(f" - Start Time Epoch: {sample.get('starttime')}")
        print(f" - End Time Epoch  : {sample.get('endtime')}")
        print(f" - Status          : {sample.get('status', 'RUNNING')}")
except Exception as e:
    print(f"Failed to query Proxmox API: {e}")