"""
Weekly Infrastructure Extractor
--------------------------------
Pulls weekly CPU/RAM (VM + node), storage capacity, and Proxmox backup job
history, and stores them as append-only weekly snapshots in SQLite.

This replaces the earlier standalone weekly_extractor.py -- that script is
retired. This is now the single extractor script.

Run this once a week (e.g. via Windows Task Scheduler, Monday morning)
covering the 7 days that just ended.

Tables covered: vm_weekly_stats, node_weekly_stats, storage_weekly_stats,
backup_weekly_runs
NOT yet included: NAS (blocked on data-quality issue -- synoRaidFreeSize >
synoRaidTotalSize, needs checking in DSM UI first), firewall
"""

import os
import re
import sqlite3
import requests
import urllib3
from urllib.parse import quote
from datetime import datetime, timedelta

# Suppress self-signed SSL warnings for local Proxmox IP
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURATION ---
PROMETHEUS_URL = "http://10.1.10.24:9090"
PROXMOX_IP = "10.1.10.2"
PVE_TOKEN_ID = "grafana_integ@pve!monitoring-read"

# SECURITY: the secret must come from an environment variable, never
# hardcoded in the script. Before running this, set it once via:
#   setx PVE_TOKEN_SECRET "your-new-secret-here"
# (then open a new terminal so the env var takes effect)
#
# IMPORTANT: the previous secret was pasted into a chat and should be
# treated as compromised -- rotate/revoke it in Proxmox and generate a new
# token before relying on this script.
PVE_TOKEN_SECRET = os.environ.get("PVE_TOKEN_SECRET")

DB_FILE = "C:\\scripts\\infra_history.db"


def get_current_week_start():
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%Y-%m-%d")


def get_week_bounds():
    """Returns (week_start_date, unix_start, unix_end) for the 7 days that
    just ended (last Monday 00:00 through this Monday 00:00) -- NOT the
    upcoming week. This matches what the report generator expects when it
    filters backup_weekly_runs by [prev_monday, curr_monday).

    BUG FIX: this previously computed [this_monday, this_monday + 7 days),
    which is the UPCOMING week, not the week that just ended -- meaning
    any run on a day other than exactly Monday pulled tasks from the wrong
    window entirely, causing extracted data to never match what the report
    was looking for."""
    week_start_str = get_current_week_start()  # this week's Monday
    this_monday_dt = datetime.strptime(week_start_str, "%Y-%m-%d")
    last_monday_dt = this_monday_dt - timedelta(days=7)
    return week_start_str, int(last_monday_dt.timestamp()), int(this_monday_dt.timestamp())


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS vm_weekly_stats (
        week_start TEXT,
        vmid TEXT,
        type TEXT,
        vm_name TEXT,
        avg_cpu_pct REAL,
        max_cpu_pct REAL,
        avg_mem_pct REAL,
        max_mem_pct REAL,
        vcpu_count REAL,
        PRIMARY KEY (week_start, vmid)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS node_weekly_stats (
        week_start TEXT PRIMARY KEY,
        avg_cpu_pct REAL,
        max_cpu_pct REAL,
        avg_mem_pct REAL,
        max_mem_pct REAL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS storage_weekly_stats (
        week_start TEXT,
        storage_name TEXT,
        used_bytes REAL,
        total_bytes REAL,
        used_pct REAL,
        max_used_pct REAL,
        PRIMARY KEY (week_start, storage_name)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS backup_weekly_runs (
        week_start TEXT,
        vmid TEXT,
        start_time TEXT,
        end_time TEXT,
        duration_mins REAL,
        status TEXT,
        PRIMARY KEY (week_start, vmid, start_time)
    )
    """)

    conn.commit()
    conn.close()


def query_prom(query):
    try:
        res = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={'query': query}, timeout=10)
        res.raise_for_status()
        data = res.json()
        if data.get('status') == 'success':
            return data['data']['result']
    except Exception as e:
        print(f"Prometheus query error [{query}]: {e}")
    return []


def parse_vmid(task):
    if task.get('id'):
        return str(task['id'])
    upid = task.get('upid', '')
    parts = upid.split(':')
    if len(parts) >= 7 and parts[5] == 'vzdump':
        return parts[6] if parts[6] else 'NODE_BATCH'
    return 'NODE_BATCH'


def get_task_log(upid, headers):
    """Fetch the full log text for a Proxmox task (limit=0 means no cap)."""
    url = f"https://{PROXMOX_IP}:8006/api2/json/nodes/proxmox/tasks/{quote(upid, safe='')}/log?limit=0"
    res = requests.get(url, headers=headers, verify=False, timeout=15)
    res.raise_for_status()
    return [entry.get('t', '') for entry in res.json().get('data', [])]


# vzdump multi-VM batch jobs run one task that backs up several VMs in
# sequence. The top-level task list only shows the outer batch job (which is
# why per-VM rows were previously all lumped under vmid='NODE_BATCH'). This
# parses the task's own log text to recover individual per-VM start/end/
# status lines, based on standard vzdump log line conventions:
#   "100: 2026-08-25 22:00:01 INFO: Starting Backup of VM 100 (qemu)"
#   "100: 2026-08-25 22:05:00 INFO: Finished Backup of VM 100 (00:05:00)"
#   "105: ... ERROR: Backup of VM 105 failed - <reason>"
# NOTE: this is a best-effort parse based on common vzdump log conventions
# -- verify against a real log sample from this environment, since exact
# wording can vary slightly across Proxmox versions.
_START_RE = re.compile(r"^(\d+):\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+INFO: Starting Backup of VM \1")
_FINISH_RE = re.compile(r"^(\d+):\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+INFO: Finished Backup of VM \1 \((\d{2}):(\d{2}):(\d{2})\)")
_ERROR_RE = re.compile(r"^(\d+):\s+.*ERROR: Backup of VM \1 failed")


def parse_vzdump_log(lines):
    """Returns a list of {vmid, start_time, end_time, duration_mins, status}
    dicts, one per VM found in the batch task's log. Empty list if no
    per-VM lines were recognized (caller should fall back to the batch-level
    row in that case, rather than losing the data entirely)."""
    per_vm = {}
    for line in lines:
        m = _START_RE.match(line)
        if m:
            vmid, ts = m.group(1), m.group(2)
            per_vm.setdefault(vmid, {})['start_time'] = ts
            per_vm[vmid].setdefault('status', 'UNKNOWN')
            continue
        m = _FINISH_RE.match(line)
        if m:
            vmid, ts, hh, mm, ss = m.groups()
            entry = per_vm.setdefault(vmid, {})
            entry['end_time'] = ts
            entry['duration_mins'] = int(hh) * 60 + int(mm) + int(ss) / 60.0
            entry['status'] = 'OK'
            continue
        m = _ERROR_RE.match(line)
        if m:
            per_vm.setdefault(m.group(1), {})['status'] = 'FAILED'

    results = []
    for vmid, entry in per_vm.items():
        results.append({
            'vmid': vmid,
            'start_time': entry.get('start_time'),
            'end_time': entry.get('end_time'),
            'duration_mins': entry.get('duration_mins'),
            'status': entry.get('status', 'UNKNOWN'),
        })
    return results


def run_extraction():
    week_start, unix_start, unix_end = get_week_bounds()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    print(f"=== Running Infra Extraction for Week of {week_start} ===")

    # --- 1. VM / LXC CPU & RAM ---
    print("1/4 Extracting VM & LXC CPU/RAM...")
    # NOTE 1: proxmox_vm_cpu_percent is a ratio (0-1) despite the name --
    # confirmed against the real Proxmox UI -- multiply by 100.
    # NOTE 2: that raw percentage is relative to a SINGLE core, not the VM's
    # total allocated vCPUs -- a VM with N vCPUs fully busy can show N*100%.
    # proxmox_vm_cpus_percent (misleadingly named) actually reports the
    # VM's allocated vCPU COUNT, not a percentage -- confirmed by its values
    # (4, 8, 16, 2 etc, matching real vCPU counts, not percentages).
    # We use it here purely as the normalization divisor so the final
    # number means "% of what's actually allocated to this VM" -- directly
    # comparable across VMs with different vCPU counts, and consistent with
    # how the flagging thresholds are meant to be read.
    cpu_avg_raw = {r['metric']['vmid']: float(r['value'][1]) * 100 for r in query_prom('avg_over_time(proxmox_vm_cpu_percent{job="proxmox-ve"}[7d])')}
    cpu_max_raw = {r['metric']['vmid']: float(r['value'][1]) * 100 for r in query_prom('max_over_time(proxmox_vm_cpu_percent{job="proxmox-ve"}[7d])')}
    vcpu_count = {r['metric']['vmid']: float(r['value'][1]) for r in query_prom('proxmox_vm_cpus_percent{job="proxmox-ve"}')}

    def normalize_cpu(vmid, raw_pct_map):
        raw = raw_pct_map.get(vmid)
        if raw is None:
            return 0.0
        cores = vcpu_count.get(vmid)
        if not cores or cores <= 0:
            return raw  # fallback: no core count found, leave un-normalized
        return raw / cores

    mem_avg = {r['metric']['vmid']: float(r['value'][1]) for r in query_prom('avg_over_time((proxmox_vm_mem_bytes / proxmox_vm_maxmem_bytes * 100)[7d:])')}
    mem_max = {r['metric']['vmid']: float(r['value'][1]) for r in query_prom('max_over_time((proxmox_vm_mem_bytes / proxmox_vm_maxmem_bytes * 100)[7d:])')}

    meta = {r['metric']['vmid']: {'name': r['metric'].get('name', 'unknown'), 'type': r['metric'].get('type', 'unknown')}
            for r in query_prom('proxmox_vm_uptime_seconds{job="proxmox-ve"}')}

    for vmid in cpu_avg_raw.keys():
        vm_meta = meta.get(vmid, {'name': 'unknown', 'type': 'unknown'})
        norm_avg = normalize_cpu(vmid, cpu_avg_raw)
        norm_max = normalize_cpu(vmid, cpu_max_raw)
        cursor.execute("""
            INSERT OR REPLACE INTO vm_weekly_stats
            (week_start, vmid, type, vm_name, avg_cpu_pct, max_cpu_pct, avg_mem_pct, max_mem_pct, vcpu_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            week_start, vmid, vm_meta['type'], vm_meta['name'],
            round(norm_avg, 2), round(norm_max, 2),
            round(mem_avg.get(vmid, 0.0), 2), round(mem_max.get(vmid, 0.0), 2),
            vcpu_count.get(vmid)
        ))

    # --- 2. NODE CPU & RAM ---
    print("2/4 Extracting Hypervisor Node CPU/RAM...")
    # Same ratio-vs-percent issue as VM CPU -- multiply by 100.
    n_cpu_avg = query_prom('avg_over_time(proxmox_node_cpustat_cpu_percent{job="proxmox-ve"}[7d])')
    n_cpu_max = query_prom('max_over_time(proxmox_node_cpustat_cpu_percent{job="proxmox-ve"}[7d])')
    n_mem_avg = query_prom('avg_over_time((proxmox_node_memory_memused_bytes / proxmox_node_memory_memtotal_bytes * 100)[7d:])')
    n_mem_max = query_prom('max_over_time((proxmox_node_memory_memused_bytes / proxmox_node_memory_memtotal_bytes * 100)[7d:])')

    if n_cpu_avg:
        cursor.execute("""
            INSERT OR REPLACE INTO node_weekly_stats
            (week_start, avg_cpu_pct, max_cpu_pct, avg_mem_pct, max_mem_pct)
            VALUES (?, ?, ?, ?, ?)
        """, (
            week_start,
            round(float(n_cpu_avg[0]['value'][1]) * 100, 2),
            round(float(n_cpu_max[0]['value'][1]) * 100, 2) if n_cpu_max else 0.0,
            round(float(n_mem_avg[0]['value'][1]), 2) if n_mem_avg else 0.0,
            round(float(n_mem_max[0]['value'][1]), 2) if n_mem_max else 0.0
        ))

    # --- 3. STORAGE POOLS ---
    print("3/4 Extracting Storage Pool capacity...")
    s_total = {r['metric']['storage']: float(r['value'][1]) for r in query_prom('proxmox_storage_total_bytes{job="proxmox-ve"}')}
    s_used_avg = {r['metric']['storage']: float(r['value'][1]) for r in query_prom('avg_over_time(proxmox_storage_used_bytes{job="proxmox-ve"}[7d])')}
    s_used_pct = {r['metric']['storage']: float(r['value'][1]) for r in query_prom(
        'avg_over_time((proxmox_storage_used_bytes{job="proxmox-ve"} / proxmox_storage_total_bytes{job="proxmox-ve"} * 100)[7d:])'
    )}
    s_used_pct_max = {r['metric']['storage']: float(r['value'][1]) for r in query_prom(
        'max_over_time((proxmox_storage_used_bytes{job="proxmox-ve"} / proxmox_storage_total_bytes{job="proxmox-ve"} * 100)[7d:])'
    )}

    for storage_name, total_bytes in s_total.items():
        cursor.execute("""
            INSERT OR REPLACE INTO storage_weekly_stats
            (week_start, storage_name, used_bytes, total_bytes, used_pct, max_used_pct)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            week_start, storage_name,
            round(s_used_avg.get(storage_name, 0.0), 2),
            round(total_bytes, 2),
            round(s_used_pct.get(storage_name, 0.0), 2),
            round(s_used_pct_max.get(storage_name, 0.0), 2),
        ))

    # --- 4. PROXMOX BACKUPS ---
    print("4/4 Extracting Proxmox Backups...")
    if not PVE_TOKEN_SECRET:
        print("  !! PVE_TOKEN_SECRET environment variable not set -- skipping backup extraction.")
        print("     Set it with: setx PVE_TOKEN_SECRET \"your-secret\"  (then reopen the terminal)")
    else:
        pve_headers = {"Authorization": f"PVEAPIToken={PVE_TOKEN_ID}={PVE_TOKEN_SECRET}"}
        # FIX: previously limit=50 with no time bound -- could silently drop
        # tasks if more than 50 vzdump jobs ran in the window. Now bounded by
        # since/until covering exactly this week, with a much higher limit
        # as a safety margin rather than the sole cutoff.
        pve_url = (
            f"https://{PROXMOX_IP}:8006/api2/json/nodes/proxmox/tasks"
            f"?limit=2000&typefilter=vzdump&since={unix_start}&until={unix_end}"
        )

        try:
            res = requests.get(pve_url, headers=pve_headers, verify=False, timeout=15)
            res.raise_for_status()
            tasks = res.json().get('data', [])

            per_vm_rows_written = 0
            batch_rows_written = 0

            for task in tasks:
                start_ts = task.get('starttime')
                end_ts = task.get('endtime')
                status = task.get('status', 'OK')
                upid = task.get('upid', '')

                # Try to get real per-VM breakdown from the task's own log.
                # Fall back to the single batch-level row (old behavior) if
                # the log can't be fetched or no per-VM lines are recognized
                # -- this way a parsing miss never means losing the data
                # entirely, it just falls back to less granular reporting.
                parsed_vms = []
                if upid:
                    try:
                        log_lines = get_task_log(upid, pve_headers)
                        parsed_vms = parse_vzdump_log(log_lines)
                    except Exception as log_err:
                        print(f"  (could not fetch/parse log for task {upid}: {log_err})")

                if parsed_vms:
                    for vm_entry in parsed_vms:
                        # Fall back to the outer task's own start/end if a
                        # per-VM timestamp is missing (e.g. it crashed before
                        # ever logging a "Finished" line).
                        start_str = vm_entry['start_time'] or (
                            datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M:%S') if start_ts else None
                        )
                        end_str = vm_entry['end_time'] or (
                            datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M:%S') if end_ts else None
                        )
                        if not start_str:
                            continue  # nothing usable to key this row on
                        cursor.execute("""
                            INSERT OR REPLACE INTO backup_weekly_runs
                            (week_start, vmid, start_time, end_time, duration_mins, status)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            week_start, vm_entry['vmid'], start_str, end_str,
                            vm_entry['duration_mins'], vm_entry['status']
                        ))
                        per_vm_rows_written += 1
                elif start_ts and end_ts:
                    # Fallback: no per-VM lines recognized -- keep the old
                    # batch-level row so at least the run itself is visible.
                    vmid = parse_vmid(task)
                    start_str = datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M:%S')
                    end_str = datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M:%S')
                    duration_mins = round((end_ts - start_ts) / 60.0, 2)
                    cursor.execute("""
                        INSERT OR REPLACE INTO backup_weekly_runs
                        (week_start, vmid, start_time, end_time, duration_mins, status)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (week_start, vmid, start_str, end_str, duration_mins, status))
                    batch_rows_written += 1

            print(f"  -> {len(tasks)} backup task(s) found; "
                  f"{per_vm_rows_written} per-VM row(s), "
                  f"{batch_rows_written} batch-level fallback row(s) recorded")
        except Exception as e:
            print(f"Proxmox Backup extraction failed: {e}")

    conn.commit()
    conn.close()
    print("=== Complete! CPU, RAM, Storage, and Backup data saved to SQLite ===")


if __name__ == "__main__":
    init_db()
    run_extraction()