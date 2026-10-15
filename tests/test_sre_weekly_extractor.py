
import sqlite3
from datetime import datetime, timedelta

import SREweeklyExtractor as extractor


def test_week_bounds_cover_previous_completed_week():
    week_start, unix_start, unix_end = extractor.get_week_bounds()

    monday = datetime.strptime(week_start, "%Y-%m-%d")
    expected_monday = datetime.now().date()
    expected_monday -= timedelta(days=expected_monday.weekday())

    assert monday.date() == expected_monday
    assert unix_end - unix_start == 7 * 24 * 60 * 60

    previous_monday = monday - timedelta(days=7)
    assert datetime.fromtimestamp(unix_start).date() == previous_monday.date()
    assert datetime.fromtimestamp(unix_end).date() == monday.date()


def test_parse_vmid_uses_task_id():
    assert extractor.parse_vmid({"id": 100}) == "100"


def test_parse_vmid_extracts_vzdump_vm_id():
    task = {
        "upid": "UPID:proxmox:123:456:789:vzdump:105:root@pam:"
    }

    assert extractor.parse_vmid(task) == "105"


def test_parse_vmid_falls_back_to_node_batch():
    assert extractor.parse_vmid({}) == "NODE_BATCH"

    task = {"upid": "UPID:proxmox:123:456:789:aptupdate::root@pam:"}
    assert extractor.parse_vmid(task) == "NODE_BATCH"


def test_parse_successful_vzdump_log():
    lines = [
        "100: 2026-08-25 22:00:01 INFO: Starting Backup of VM 100 (qemu)",
        "100: 2026-08-25 22:05:00 INFO: Finished Backup of VM 100 (00:05:00)",
    ]

    result = extractor.parse_vzdump_log(lines)

    assert len(result) == 1
    assert result[0]["vmid"] == "100"
    assert result[0]["start_time"] == "2026-08-25 22:00:01"
    assert result[0]["end_time"] == "2026-08-25 22:05:00"
    assert result[0]["duration_mins"] == 5.0
    assert result[0]["status"] == "OK"


def test_parse_backup_duration_with_seconds():
    lines = [
        "105: 2026-08-25 22:00:01 INFO: Starting Backup of VM 105 (qemu)",
        "105: 2026-08-25 22:05:31 INFO: Finished Backup of VM 105 (00:05:30)",
    ]

    result = extractor.parse_vzdump_log(lines)

    assert result[0]["duration_mins"] == 5.5


def test_parse_failed_backup():
    lines = [
        "105: 2026-08-25 22:00:01 INFO: Starting Backup of VM 105 (qemu)",
        "105: 2026-08-25 22:02:00 ERROR: Backup of VM 105 failed - test error",
    ]

    result = extractor.parse_vzdump_log(lines)

    assert len(result) == 1
    assert result[0]["vmid"] == "105"
    assert result[0]["status"] == "FAILED"


def test_parse_multiple_vms_in_batch():
    lines = [
        "100: 2026-08-25 22:00:01 INFO: Starting Backup of VM 100 (qemu)",
        "100: 2026-08-25 22:05:00 INFO: Finished Backup of VM 100 (00:05:00)",
        "105: 2026-08-25 22:05:01 INFO: Starting Backup of VM 105 (qemu)",
        "105: 2026-08-25 22:10:01 INFO: Finished Backup of VM 105 (00:05:00)",
    ]

    result = extractor.parse_vzdump_log(lines)

    assert len(result) == 2
    assert {row["vmid"] for row in result} == {"100", "105"}
    assert all(row["status"] == "OK" for row in result)


def test_unrecognized_log_returns_empty_list():
    assert extractor.parse_vzdump_log(["Some unrelated log entry"]) == []


def test_init_db_creates_expected_tables(tmp_path, monkeypatch):
    test_db = tmp_path / "test_infra_history.db"
    monkeypatch.setattr(extractor, "DB_FILE", str(test_db))

    extractor.init_db()

    with sqlite3.connect(test_db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "vm_weekly_stats",
        "node_weekly_stats",
        "storage_weekly_stats",
        "backup_weekly_runs",
    }.issubset(tables)
