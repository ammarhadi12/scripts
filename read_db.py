import sqlite3
conn = sqlite3.connect("C:\\scripts\\infra_history.db")
conn.execute("ALTER TABLE vm_weekly_stats ADD COLUMN vcpu_count REAL")
conn.commit()
conn.close()