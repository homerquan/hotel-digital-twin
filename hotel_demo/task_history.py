"""Archive observed task outcomes and summarize performance across runs."""

import sqlite3
import time

OUTCOMES = {"completed": 1, "failed": 0, "killed": 0}
RECENT_LIMIT = 15
ROLLING_WINDOW = 5


class TaskHistory:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS outcomes (
                task TEXT PRIMARY KEY,
                preset TEXT NOT NULL,
                success INTEGER NOT NULL,
                seconds REAL,
                recorded_at REAL NOT NULL
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS outcomes_by_type ON outcomes(preset, recorded_at)")
        self.cached = None

    def record(self, task):
        preset = task.get("preset")
        outcome = OUTCOMES.get(task["status"])
        if preset is None or outcome is None:
            return
        seconds = max(0.0, task["ended_at"] - task["time"]) if outcome else None
        with self.db:
            result = self.db.execute("""
                INSERT INTO outcomes VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task) DO UPDATE SET
                    preset = excluded.preset, success = excluded.success, seconds = excluded.seconds
                WHERE outcomes.preset <> excluded.preset OR outcomes.success <> excluded.success
                    OR outcomes.seconds IS NOT excluded.seconds
            """, (task["id"], preset, outcome, seconds, time.time()))
        if result.rowcount:
            self.cached = None

    def summary(self):
        if self.cached is not None:
            return self.cached
        result = {}
        for preset, total, successes, average in self.db.execute("""
            SELECT preset, count(*), sum(success), avg(seconds) FROM outcomes GROUP BY preset
        """):
            rows = list(reversed(self.db.execute("""
                SELECT success, seconds, recorded_at FROM outcomes WHERE preset = ?
                ORDER BY recorded_at DESC, rowid DESC LIMIT ?
            """, (preset, RECENT_LIMIT + ROLLING_WINDOW - 1)).fetchall()))
            recent = []
            for i in range(max(0, len(rows) - RECENT_LIMIT), len(rows)):
                window = rows[max(0, i - ROLLING_WINDOW + 1):i + 1]
                times = [seconds for _, seconds, _ in window if seconds is not None]
                recent.append({
                    "at": rows[i][2],
                    "rate": 100 * sum(row[0] for row in window) / len(window),
                    "average": sum(times) / len(times) if times else None,
                })
            result[preset] = {
                "total": total, "completed": successes, "rate": 100 * successes / total,
                "average": average, "recent": recent,
            }
        self.cached = result
        return result

    def close(self):
        self.db.close()
