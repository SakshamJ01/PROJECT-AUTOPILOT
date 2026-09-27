import sqlite3
import json
from pathlib import Path
from autopilot.core.config import CONFIG

conn = sqlite3.connect(CONFIG.db_path)
cur = conn.cursor()
cur.execute("PRAGMA table_info(workflow_events)")
print("workflow_events columns:", cur.fetchall())

cur.execute("SELECT * FROM workflow_events WHERE job_id LIKE '%d0124008%' ORDER BY rowid ASC")
events = cur.fetchall()
print(f"EVENTS ({len(events)}):")
for e in events:
    print(" ", e)

job_dir = CONFIG.artifacts_dir / "jobs" / "prod-What-is-Ohm's-Law-actually-d0124008"
print("\nJOB ARTIFACTS DIR:", job_dir)
if job_dir.exists():
    for f in job_dir.rglob("*"):
        if f.is_file():
            print(f"  {f.relative_to(job_dir)} ({f.stat().st_size:,} bytes)")
else:
    print("  Job dir does not exist")
