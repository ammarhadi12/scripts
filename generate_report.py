"""
Weekly Infrastructure Report -- PDF Generator
-----------------------------------------------
Reads from infra_history.db (populated by weekly_extractor / SREweeklyExtractor)
and produces a short, table-and-chart PDF -- no long prose.

Sections:
  1. Needs Attention (auto-flagged from thresholds below)
  2. Hypervisor Node summary
  3. Storage capacity (with trend chart if 2+ weeks of history exist)
  4. Top 5 VMs by CPU, Top 5 Containers (LXC) by CPU
  5. Backup execution summary

Thresholds (agreed):
  CPU (VM or node): flag if avg > 75% or peak > 90%
  Storage used %:   80% = warning, 90% = critical
  RAM (VM or node): trend only, never flagged (hypervisor-allocated view only,
                     not a reliable signal of actual in-guest pressure)
"""

import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
)

DB_FILE = "C:\\scripts\\infra_history.db"
OUTPUT_DIR = "C:\\scripts"
CHART_TMP = "C:\\scripts\\_storage_trend_chart.png"
PAGE_SIZE = landscape(letter)

# --- Thresholds ---
CPU_AVG_WARN = 75
CPU_PEAK_WARN = 90
STORAGE_WARN = 80
STORAGE_CRITICAL = 90


def format_bytes(num_bytes):
    """Auto-scale a byte count to the most readable unit (KB/MB/GB/TB)."""
    if num_bytes is None:
        return "-"
    b = float(num_bytes)
    if b >= 1024 ** 4:
        return f"{b / 1024 ** 4:.2f} TB"
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.2f} GB"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.2f} MB"
    if b >= 1024:
        return f"{b / 1024:.2f} KB"
    return f"{b:.0f} B"


def get_week_dates():
    today = datetime.now().date()
    current_monday = today - timedelta(days=today.weekday())
    prev_monday = current_monday - timedelta(days=7)
    prev_sunday = current_monday - timedelta(days=1)
    return (
        current_monday.strftime("%Y-%m-%d"),
        prev_monday.strftime("%Y-%m-%d"),
        prev_sunday.strftime("%Y-%m-%d"),
    )


def format_duration(minutes):
    if pd.isna(minutes) or minutes is None:
        return "0h 0m"
    total_seconds = int(round(float(minutes) * 60))
    hours = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    return f"{hours}h {mins}m"


def styled_table(data, col_widths=None):
    """A consistent, minimal table style used across all sections."""
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def build_report():
    curr_monday, prev_monday, prev_sunday = get_week_dates()
    conn = sqlite3.connect(DB_FILE)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleSmall", parent=styles["Title"], fontSize=16)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, spaceBefore=14, spaceAfter=6)
    normal = styles["Normal"]
    alert_style = ParagraphStyle("Alert", parent=normal, textColor=colors.HexColor("#C0392B"), fontSize=10, leading=14)
    ok_style = ParagraphStyle("OK", parent=normal, textColor=colors.HexColor("#27AE60"), fontSize=10)

    story = []
    flags = []  # collected "needs attention" lines

    story.append(Paragraph("Weekly Infrastructure Report", title_style))
    story.append(Paragraph(f"{prev_monday} to {prev_sunday}", normal))
    story.append(Spacer(1, 10))

    # -----------------------------------------------------------------
    # Pull data needed for both the flag section and the detail sections
    # -----------------------------------------------------------------

    df_node = pd.read_sql_query(
        "SELECT * FROM node_weekly_stats WHERE week_start IN (?, ?)",
        conn, params=(curr_monday, prev_monday),
    )
    curr_node = df_node[df_node["week_start"] == curr_monday]
    prev_node = df_node[df_node["week_start"] == prev_monday]

    df_storage_curr = pd.read_sql_query(
        "SELECT * FROM storage_weekly_stats WHERE week_start = ?",
        conn, params=(curr_monday,),
    )

    try:
        df_vm = pd.read_sql_query(
            """
            SELECT curr.vmid, curr.type, curr.vm_name,
                   curr.avg_cpu_pct AS curr_cpu, prev.avg_cpu_pct AS prev_cpu,
                   curr.max_cpu_pct AS max_cpu,
                   curr.avg_mem_pct AS curr_mem, prev.avg_mem_pct AS prev_mem,
                   curr.vcpu_count AS vcpu_count
            FROM vm_weekly_stats curr
            LEFT JOIN vm_weekly_stats prev
                ON curr.vmid = prev.vmid AND prev.week_start = ?
            WHERE curr.week_start = ?
            ORDER BY curr.avg_cpu_pct DESC
            """,
            conn, params=(prev_monday, curr_monday),
        )
    except Exception:
        # Older database without the vcpu_count column yet -- fall back to
        # the query without it rather than crashing the whole report.
        df_vm = pd.read_sql_query(
            """
            SELECT curr.vmid, curr.type, curr.vm_name,
                   curr.avg_cpu_pct AS curr_cpu, prev.avg_cpu_pct AS prev_cpu,
                   curr.max_cpu_pct AS max_cpu,
                   curr.avg_mem_pct AS curr_mem, prev.avg_mem_pct AS prev_mem
            FROM vm_weekly_stats curr
            LEFT JOIN vm_weekly_stats prev
                ON curr.vmid = prev.vmid AND prev.week_start = ?
            WHERE curr.week_start = ?
            ORDER BY curr.avg_cpu_pct DESC
            """,
            conn, params=(prev_monday, curr_monday),
        )
    if not df_vm.empty:
        df_vm["prev_cpu"] = df_vm["prev_cpu"].fillna(0.0)
        df_vm["prev_mem"] = df_vm["prev_mem"].fillna(0.0)
        df_vm["cpu_wow"] = df_vm["curr_cpu"] - df_vm["prev_cpu"]
        df_vm["mem_wow"] = df_vm["curr_mem"] - df_vm["prev_mem"]

    df_backups = pd.read_sql_query(
        """SELECT * FROM backup_weekly_runs
           WHERE start_time >= ? AND start_time < ?
           ORDER BY start_time DESC""",
        conn, params=(f"{prev_monday} 00:00:00", f"{curr_monday} 00:00:00"),
    )

    # -----------------------------------------------------------------
    # 1. NEEDS ATTENTION -- auto-flagged from thresholds
    # -----------------------------------------------------------------
    if not curr_node.empty:
        n = curr_node.iloc[0]
        if n["avg_cpu_pct"] > CPU_AVG_WARN or n["max_cpu_pct"] > CPU_PEAK_WARN:
            flags.append(
                f"Hypervisor node CPU high — avg {n['avg_cpu_pct']:.1f}%, peak {n['max_cpu_pct']:.1f}%"
            )

    if not df_storage_curr.empty:
        for _, row in df_storage_curr.iterrows():
            pct = row["used_pct"]
            peak = row["max_used_pct"] if "max_used_pct" in row and pd.notna(row["max_used_pct"]) else pct
            if peak >= STORAGE_CRITICAL:
                flags.append(f"CRITICAL: storage pool '{row['storage_name']}' peaked at {peak:.1f}% used")
            elif peak >= STORAGE_WARN:
                flags.append(f"Storage pool '{row['storage_name']}' peaked at {peak:.1f}% used — approaching capacity")

    if not df_vm.empty:
        hot_vms = df_vm[(df_vm["curr_cpu"] > CPU_AVG_WARN) | (df_vm["max_cpu"] > CPU_PEAK_WARN)]
        for _, row in hot_vms.iterrows():
            flags.append(
                f"{row['vm_name']} ({row['type']}) CPU high — avg {row['curr_cpu']:.1f}%, peak {row['max_cpu']:.1f}%"
            )

    if not df_backups.empty:
        failed = df_backups[df_backups["status"] != "OK"]
        for _, row in failed.iterrows():
            flags.append(f"Backup issue — VMID {row['vmid']} status: {row['status']}")

    story.append(Paragraph("Needs Attention", h2))
    if flags:
        for f in flags:
            story.append(Paragraph(f"• {f}", alert_style))
    else:
        story.append(Paragraph("No thresholds breached this week.", ok_style))
    story.append(Spacer(1, 6))

    # -----------------------------------------------------------------
    # 2. HYPERVISOR NODE
    # -----------------------------------------------------------------
    story.append(Paragraph("Hypervisor Node", h2))
    if not curr_node.empty:
        n = curr_node.iloc[0]
        p_cpu = prev_node.iloc[0]["avg_cpu_pct"] if not prev_node.empty else None
        p_mem = prev_node.iloc[0]["avg_mem_pct"] if not prev_node.empty else None
        cpu_wow = f"{n['avg_cpu_pct'] - p_cpu:+.1f}%" if p_cpu is not None else "n/a (no prior week)"
        mem_wow = f"{n['avg_mem_pct'] - p_mem:+.1f}%" if p_mem is not None else "n/a (no prior week)"

        data = [
            ["Metric", "Avg", "Peak", "WoW (avg)"],
            ["CPU %", f"{n['avg_cpu_pct']:.1f}", f"{n['max_cpu_pct']:.1f}", cpu_wow],
            ["RAM % (trend only)", f"{n['avg_mem_pct']:.1f}", f"{n['max_mem_pct']:.1f}", mem_wow],
        ]
        story.append(styled_table(data, col_widths=[2.2 * inch, 1.5 * inch, 1.5 * inch, 1.6 * inch]))
    else:
        story.append(Paragraph("No node data available for the current week.", normal))

    # -----------------------------------------------------------------
    # 3. STORAGE
    # -----------------------------------------------------------------
    story.append(Paragraph("Storage Pools", h2))
    if not df_storage_curr.empty:
        has_max_col = "max_used_pct" in df_storage_curr.columns
        header = ["Pool", "Avg Used %", "Max Used %", "Used", "Total"] if has_max_col else \
                 ["Pool", "Used %", "Used", "Total"]
        data = [header]
        for _, row in df_storage_curr.iterrows():
            if has_max_col:
                data.append([
                    row["storage_name"],
                    f"{row['used_pct']:.1f}%",
                    f"{row['max_used_pct']:.1f}%",
                    format_bytes(row["used_bytes"]),
                    format_bytes(row["total_bytes"]),
                ])
            else:
                data.append([
                    row["storage_name"],
                    f"{row['used_pct']:.1f}%",
                    format_bytes(row["used_bytes"]),
                    format_bytes(row["total_bytes"]),
                ])
        col_widths = [2.2 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch] if has_max_col else \
                     [2.2 * inch, 1.5 * inch, 1.5 * inch, 1.5 * inch]
        story.append(styled_table(data, col_widths=col_widths))
    else:
        story.append(Paragraph("No storage data available for the current week.", normal))

    # Trend chart -- only if 2+ weeks of history exist for at least one pool
    df_storage_hist = pd.read_sql_query(
        "SELECT week_start, storage_name, used_pct FROM storage_weekly_stats ORDER BY week_start",
        conn,
    )
    weeks_available = df_storage_hist["week_start"].nunique()
    if weeks_available >= 2:
        fig, ax = plt.subplots(figsize=(8, 2.2))
        for name, grp in df_storage_hist.groupby("storage_name"):
            ax.plot(grp["week_start"], grp["used_pct"], marker="o", label=name)
        ax.set_ylabel("Used %")
        ax.set_ylim(0, 100)
        ax.legend(fontsize=7, loc="upper left")
        ax.tick_params(axis="x", rotation=45, labelsize=7)
        fig.tight_layout()
        fig.savefig(CHART_TMP, dpi=150)
        plt.close(fig)
        story.append(Spacer(1, 6))
        story.append(Image(CHART_TMP, width=8 * inch, height=2.2 * inch))
    else:
        story.append(Paragraph(
            "(Trend chart will appear once 2+ weeks of storage history are recorded.)",
            ParagraphStyle("Note", parent=normal, fontSize=8, textColor=colors.grey),
        ))

    story.append(PageBreak())

    # -----------------------------------------------------------------
    # 4. TOP 5 VMs / TOP 5 CONTAINERS (by CPU)
    # -----------------------------------------------------------------
    story.append(Paragraph("Top 5 VMs by CPU", h2))
    if not df_vm.empty:
        vms = df_vm[df_vm["type"] == "qemu"].head(5)
        if not vms.empty:
            has_vcpu = "vcpu_count" in vms.columns
            header = ["Name", "CPU % (of allocated)", "CPU WoW", "vCPUs", "RAM % (trend)", "RAM WoW"] if has_vcpu else \
                     ["Name", "CPU % (of allocated)", "CPU WoW", "RAM % (trend)", "RAM WoW"]
            data = [header]
            for _, row in vms.iterrows():
                vcpu_str = f"{int(row['vcpu_count'])}" if has_vcpu and pd.notna(row.get("vcpu_count")) else "-"
                line = [row["vm_name"][:28], f"{row['curr_cpu']:.1f}", f"{row['cpu_wow']:+.1f}"]
                if has_vcpu:
                    line.append(vcpu_str)
                line += [f"{row['curr_mem']:.1f}", f"{row['mem_wow']:+.1f}"]
                data.append(line)
            col_widths = [2.6 * inch, 1.5 * inch, 1.0 * inch, 0.8 * inch, 1.4 * inch, 1.0 * inch] if has_vcpu else \
                         [3.0 * inch, 1.5 * inch, 1.2 * inch, 1.6 * inch, 1.2 * inch]
            story.append(styled_table(data, col_widths=col_widths))
        else:
            story.append(Paragraph("No QEMU VM data this week.", normal))
    else:
        story.append(Paragraph("No VM data this week.", normal))

    story.append(Paragraph("Top 5 Containers (LXC) by CPU", h2))
    if not df_vm.empty:
        lxcs = df_vm[df_vm["type"] == "lxc"].head(5)
        if not lxcs.empty:
            has_vcpu = "vcpu_count" in lxcs.columns
            header = ["Name", "CPU % (of allocated)", "CPU WoW", "vCPUs", "RAM % (trend)", "RAM WoW"] if has_vcpu else \
                     ["Name", "CPU % (of allocated)", "CPU WoW", "RAM % (trend)", "RAM WoW"]
            data = [header]
            for _, row in lxcs.iterrows():
                vcpu_str = f"{int(row['vcpu_count'])}" if has_vcpu and pd.notna(row.get("vcpu_count")) else "-"
                line = [row["vm_name"][:28], f"{row['curr_cpu']:.1f}", f"{row['cpu_wow']:+.1f}"]
                if has_vcpu:
                    line.append(vcpu_str)
                line += [f"{row['curr_mem']:.1f}", f"{row['mem_wow']:+.1f}"]
                data.append(line)
            col_widths = [2.6 * inch, 1.5 * inch, 1.0 * inch, 0.8 * inch, 1.4 * inch, 1.0 * inch] if has_vcpu else \
                         [3.0 * inch, 1.5 * inch, 1.2 * inch, 1.6 * inch, 1.2 * inch]
            story.append(styled_table(data, col_widths=col_widths))
        else:
            story.append(Paragraph("No LXC container data this week.", normal))
    else:
        story.append(Paragraph("No container data this week.", normal))

    # -----------------------------------------------------------------
    # 5. BACKUPS
    # -----------------------------------------------------------------
    story.append(Paragraph("Backup Execution Summary", h2))
    if not df_backups.empty:
        total = len(df_backups)
        ok = len(df_backups[df_backups["status"] == "OK"])
        failed = total - ok
        avg_dur = format_duration(df_backups["duration_mins"].mean())
        story.append(Paragraph(
            f"Total runs: {total} | Successful: {ok} | Failed/Warning: {failed} | Avg duration: {avg_dur}",
            normal,
        ))
        story.append(Spacer(1, 4))

        data = [["VMID", "Start", "End", "Duration", "Status"]]
        for _, row in df_backups.iterrows():
            end_val = row["end_time"] if pd.notna(row["end_time"]) else "-"
            data.append([
                row["vmid"],
                row["start_time"],
                end_val,
                format_duration(row["duration_mins"]),
                row["status"],
            ])
        story.append(styled_table(data, col_widths=[1.0 * inch, 2.0 * inch, 2.0 * inch, 1.4 * inch, 1.2 * inch]))
    else:
        story.append(Paragraph("No backup runs recorded this week.", normal))

    conn.close()

    # Filename carries the coverage period so re-running for a different
    # week never overwrites a previous report.
    output_pdf = f"{OUTPUT_DIR}\\weekly_report_{prev_monday}_to_{prev_sunday}.pdf"

    doc = SimpleDocTemplate(
        output_pdf, pagesize=PAGE_SIZE,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
    )
    doc.build(story)
    print(f"Report written to {output_pdf}")


if __name__ == "__main__":
    build_report()