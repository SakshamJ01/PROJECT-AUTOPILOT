import sqlite3
import json
from autopilot.core.config import CONFIG

conn = sqlite3.connect(CONFIG.db_path)
cur = conn.cursor()
cur.execute("SELECT queue_id, payload_json FROM queue_items ORDER BY rowid DESC LIMIT 10")
rows = cur.fetchall()
for q_id, p_json in rows:
    if p_json and ("edge-tts" in p_json or "gemini-flash" in p_json):
        data = json.loads(p_json)
        if "tts_provider" in data and "edge" in data["tts_provider"]:
            data["tts_provider"] = "edge_tts"
        if "llm_provider" in data and "gemini" in data["llm_provider"]:
            data["llm_provider"] = "gemini"
        cur.execute("UPDATE queue_items SET payload_json=? WHERE queue_id=?", (json.dumps(data), q_id))
        print(f"Updated {q_id} -> {data}")
conn.commit()
print("Database queue items normalized.")
