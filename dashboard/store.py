"""The dashboard's own data: check-ins, counters, wins, reviews, resume tags,
lessons from closed applications, and the config file.

Everything lives in the tracker's data/dashboard/ folder, which git ignores. Nothing here
touches the tracker's database.
"""
import json
import os
import sqlite3
import time

BASE = os.path.dirname(os.path.abspath(__file__))
# Inside the tracker's data/ folder: git-ignored, and the only place the
# tracker service may write.
DATA = os.environ.get("DASHBOARD_DATA", os.path.join(BASE, "..", "data", "dashboard"))
DB = os.path.join(DATA, "dashboard.db")
CONFIG = os.path.join(DATA, "config.json")
CONFIG_EXAMPLE = os.path.join(BASE, "config.example.json")
MAX_TEXT = 1000
KINDS = ("networking", "followups")


def load_config():
    """Defaults from config.example.json, overridden by data/config.json.
    The first run copies the example so there is a file to edit."""
    with open(CONFIG_EXAMPLE) as f:
        cfg = json.load(f)
    os.makedirs(DATA, exist_ok=True)
    if not os.path.exists(CONFIG):
        with open(CONFIG, "w") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
    with open(CONFIG) as f:
        cfg.update(json.load(f))
    if not os.path.isabs(cfg["tracker_db"]):
        cfg["tracker_db"] = os.path.normpath(os.path.join(BASE, cfg["tracker_db"]))
    return cfg


def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    os.makedirs(DATA, exist_ok=True)
    db = connect()
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS counters (
            date TEXT NOT NULL, kind TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (date, kind));
        CREATE TABLE IF NOT EXISTS checkins (
            date TEXT PRIMARY KEY, energy INTEGER NOT NULL,
            activity TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
            updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS wins (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
            text TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
        -- week is the Monday of the reviewed week, YYYY-MM-DD.
        CREATE TABLE IF NOT EXISTS reviews (
            week TEXT PRIMARY KEY, moved TEXT NOT NULL DEFAULT '',
            worked TEXT NOT NULL DEFAULT '', change TEXT NOT NULL DEFAULT '',
            proud TEXT NOT NULL DEFAULT '', updated REAL NOT NULL);
        -- Keyed by the tracker's row id; the tracker itself is never changed.
        CREATE TABLE IF NOT EXISTS resume_tags (
            job_id TEXT PRIMARY KEY, version TEXT NOT NULL, updated REAL NOT NULL);
        -- One line per closed application (reason: rejected or ghosted).
        CREATE TABLE IF NOT EXISTS lessons (
            job_id TEXT NOT NULL, reason TEXT NOT NULL, text TEXT NOT NULL,
            ts REAL NOT NULL, PRIMARY KEY (job_id, reason));
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    # Status changes before the dashboard existed are not asked about.
    db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('baseline', ?)", (str(time.time()),))
    db.commit()
    db.close()


def text(v):
    return " ".join(str(v or "").split())[:MAX_TEXT]


class Store:
    """A short-lived connection: open per request, close when done."""

    def __enter__(self):
        self.db = connect()
        return self

    def __exit__(self, *exc):
        if exc[0] is None:
            self.db.commit()
        self.db.close()

    def baseline(self):
        return float(self.db.execute("SELECT value FROM meta WHERE key = 'baseline'").fetchone()[0])

    # Done-line counters (networking touches, follow-ups sent)
    def counts(self, date):
        out = {k: 0 for k in KINDS}
        out.update(dict(self.db.execute("SELECT kind, n FROM counters WHERE date = ?", (date,)).fetchall()))
        return out

    def counts_between(self, start, end):
        out = {k: 0 for k in KINDS}
        out.update(dict(self.db.execute("SELECT kind, SUM(n) FROM counters WHERE date BETWEEN ? AND ? GROUP BY kind",
                                        (start, end)).fetchall()))
        return out

    def bump(self, date, kind, delta):
        if kind not in KINDS or delta not in (1, -1):
            raise ValueError("unknown counter")
        self.db.execute("INSERT OR IGNORE INTO counters (date, kind, n) VALUES (?, ?, 0)", (date, kind))
        self.db.execute("UPDATE counters SET n = MAX(0, n + ?) WHERE date = ? AND kind = ?", (delta, date, kind))

    # Energy and recovery check-ins
    def checkin(self, date):
        r = self.db.execute("SELECT * FROM checkins WHERE date = ?", (date,)).fetchone()
        return dict(r) if r else None

    def checkins(self, start, end):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM checkins WHERE date BETWEEN ? AND ? ORDER BY date", (start, end))]

    def recent_checkins(self, n):
        return [dict(r) for r in self.db.execute("SELECT * FROM checkins ORDER BY date DESC LIMIT ?", (n,))]

    def save_checkin(self, date, energy, activity, note):
        if energy not in (1, 2, 3, 4, 5):
            raise ValueError("Pick an energy level from 1 to 5.")
        self.db.execute("""INSERT INTO checkins (date, energy, activity, note, updated) VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT (date) DO UPDATE SET energy = excluded.energy, activity = excluded.activity,
                           note = excluded.note, updated = excluded.updated""",
                        (date, energy, text(activity), text(note), time.time()))

    # Wins
    def wins(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM wins WHERE deleted = 0 ORDER BY ts DESC")]

    def add_win(self, t):
        t = text(t)
        if not t:
            raise ValueError("Type the win first.")
        self.db.execute("INSERT INTO wins (ts, text) VALUES (?, ?)", (time.time(), t))

    def set_win_deleted(self, win_id, deleted):
        self.db.execute("UPDATE wins SET deleted = ? WHERE id = ?", (int(deleted), win_id))

    # Weekly reviews
    def review(self, week):
        r = self.db.execute("SELECT * FROM reviews WHERE week = ?", (week,)).fetchone()
        return dict(r) if r else None

    def save_review(self, week, moved, worked, change, proud):
        self.db.execute("""INSERT INTO reviews (week, moved, worked, change, proud, updated) VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT (week) DO UPDATE SET moved = excluded.moved, worked = excluded.worked,
                           change = excluded.change, proud = excluded.proud, updated = excluded.updated""",
                        (week, text(moved), text(worked), text(change), text(proud), time.time()))

    # Resume version per application
    def resume_tags(self):
        return dict(self.db.execute("SELECT job_id, version FROM resume_tags").fetchall())

    def tag_resume(self, job_id, version):
        version = text(version)[:40]
        if version:
            self.db.execute("""INSERT INTO resume_tags (job_id, version, updated) VALUES (?, ?, ?)
                               ON CONFLICT (job_id) DO UPDATE SET version = excluded.version,
                               updated = excluded.updated""", (job_id, version, time.time()))
        else:
            self.db.execute("DELETE FROM resume_tags WHERE job_id = ?", (job_id,))

    # Lessons from rejected or ghosted applications
    def lessons_answered(self):
        return {(r["job_id"], r["reason"]) for r in self.db.execute("SELECT job_id, reason FROM lessons")}

    def save_lesson(self, job_id, reason, t):
        if reason not in ("rejected", "ghosted"):
            raise ValueError("unknown reason")
        self.db.execute("INSERT OR IGNORE INTO lessons (job_id, reason, text, ts) VALUES (?, ?, ?, ?)",
                        (job_id, reason, text(t) or "nothing, move on", time.time()))
