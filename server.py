#!/usr/bin/env python3
"""Job search tracker: one page plus a small JSON API over SQLite.

Serves static/index.html and keeps the tracker rows in data/tracker.db so
every device on the home network sees the same list. Python standard library
only. Run by job-tracker.service on port 80 with no login, as the owner asked,
so it only answers clients on private (home network) addresses.

  GET    /api/jobs               all current rows
  PUT    /api/jobs/<id>          create or update one row
  DELETE /api/jobs/<id>          remove a row (kept in the database, restorable)
  POST   /api/jobs/<id>/restore  bring a removed row back
  POST   /api/import             replace the list with {"rows": [...]}
  GET    /api/history?limit=N    recent changes, newest first (&major=1: only
                                 status changes, adds, deletes and restores)
  GET    /api/log                all current log entries (the Log page)
  PUT    /api/log/<id>           create or update one log entry
  DELETE /api/log/<id>           remove a log entry (kept, restorable)
  POST   /api/log/<id>/restore   bring a removed log entry back
"""
import ipaddress
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
DATA = os.path.join(BASE, "data")
DB = os.path.join(DATA, "tracker.db")
SEED = os.path.join(DATA, "seed.json")
BACKUPS = os.path.join(DATA, "backups")
PORT = int(os.environ.get("PORT", "80"))

FIELDS = ["company", "role", "status", "applied", "contact", "next", "followup", "notes"]
LABELS = {"company": "Company", "role": "Role", "status": "Status", "applied": "Applied",
          "contact": "Contact", "next": "Next action", "followup": "Follow-up", "notes": "Notes"}
STATUSES = ["Researching", "Preparing", "Applied", "Recruiter screen", "Interviewing",
            "Awaiting decision", "Offer", "Rejected", "Closed / filled", "Withdrawn"]
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_BODY = 1_000_000
MAX_FIELD = 4000
MERGE_SECONDS = 300   # edits to one field within this window are one history entry
KEEP_BACKUPS = 30

HOSTNAMES = {"localhost", socket.gethostname().lower(), socket.gethostname().lower() + ".local"}
HOSTNAMES |= {h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}

lock = threading.Lock()


def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    os.makedirs(DATA, exist_ok=True)
    db = connect()
    cols = ", ".join(f'"{f}" TEXT NOT NULL DEFAULT \'\'' for f in FIELDS)
    db.executescript(f"""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, {cols},
            created REAL NOT NULL, updated REAL NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS history (
            n INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, job_id TEXT NOT NULL,
            action TEXT NOT NULL, field TEXT NOT NULL DEFAULT '',
            old TEXT NOT NULL DEFAULT '', new TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS history_job ON history (job_id, field, n);
        -- Free-form log entries. link_type: '' (nothing), 'company' or 'job'.
        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY, ts REAL NOT NULL, text TEXT NOT NULL DEFAULT '',
            link_type TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '',
            job_id TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL, updated REAL NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0);
    """)
    if db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0 and os.path.exists(SEED):
        with open(SEED) as f:
            rows = json.load(f).get("rows", [])
        now = time.time()
        for r in rows:
            r = clean(r)
            if not ID_RE.match(str(r.get("id", ""))):
                continue
            db.execute(f'INSERT INTO jobs (id, {qcols()}, created, updated) VALUES (?, {marks()}, ?, ?)',
                       [r["id"]] + [r[f] for f in FIELDS] + [now, now])
            db.execute("INSERT INTO history (ts, job_id, action) VALUES (?, ?, 'imported')", (now, r["id"]))
    db.commit()
    db.close()


def qcols():
    return ", ".join(f'"{f}"' for f in FIELDS)


def marks():
    return ", ".join("?" for _ in FIELDS)


def clean(r):
    """Coerce one row from a client into the stored shape."""
    out = {"id": str(r.get("id", ""))} if isinstance(r, dict) else {"id": ""}
    for f in FIELDS:
        v = r.get(f, "") if isinstance(r, dict) else ""
        out[f] = ("" if v is None else str(v))[:MAX_FIELD]
    if out["status"] not in STATUSES:
        out["status"] = "Researching"
    return out


def row_dict(row):
    return {"id": row["id"], **{f: row[f] for f in FIELDS}}


def log(db, job_id, action, field="", old="", new=""):
    now = time.time()
    if action == "changed":
        last = db.execute("SELECT n, ts, old, action FROM history WHERE job_id=? AND field=? ORDER BY n DESC LIMIT 1",
                          (job_id, field)).fetchone()
        if last and last["action"] == "changed" and now - last["ts"] < MERGE_SECONDS:
            if last["old"] == new:      # typed something and put it back
                db.execute("DELETE FROM history WHERE n=?", (last["n"],))
            else:
                db.execute("UPDATE history SET new=?, ts=? WHERE n=?", (new, now, last["n"]))
            return
    db.execute("INSERT INTO history (ts, job_id, action, field, old, new) VALUES (?, ?, ?, ?, ?, ?)",
               (now, job_id, action, field, old, new))


def upsert(db, r):
    now = time.time()
    cur = db.execute("SELECT * FROM jobs WHERE id=?", (r["id"],)).fetchone()
    if cur is None:
        db.execute(f'INSERT INTO jobs (id, {qcols()}, created, updated) VALUES (?, {marks()}, ?, ?)',
                   [r["id"]] + [r[f] for f in FIELDS] + [now, now])
        log(db, r["id"], "added")
        return
    if cur["deleted"]:
        log(db, r["id"], "restored")
    for f in FIELDS:
        if cur[f] != r[f]:
            log(db, r["id"], "changed", f, cur[f], r[f])
    sets = ", ".join(f'"{f}"=?' for f in FIELDS)
    db.execute(f"UPDATE jobs SET {sets}, updated=?, deleted=0 WHERE id=?", [r[f] for f in FIELDS] + [now, r["id"]])


def backup_daily():
    """Keep one database copy per day; called at start and then hourly."""
    try:
        os.makedirs(BACKUPS, exist_ok=True)
        target = os.path.join(BACKUPS, time.strftime("tracker-%Y-%m-%d.db"))
        if not os.path.exists(target):
            src, dst = connect(), sqlite3.connect(target)
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
        old = sorted(f for f in os.listdir(BACKUPS) if f.startswith("tracker-") and f.endswith(".db"))
        for f in old[:-KEEP_BACKUPS]:
            os.remove(os.path.join(BACKUPS, f))
    except Exception as e:   # a failed backup must not take the tracker down
        print(f"backup failed: {e}", file=sys.stderr)
    t = threading.Timer(3600, backup_daily)
    t.daemon = True
    t.start()


def private_ip(text):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


def host_only(header):
    """Host header without its port: 'pi.local:80' -> 'pi.local', '[::1]:80' -> '::1'."""
    host = header.strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class Handler(BaseHTTPRequestHandler):
    server_version = "JobTracker/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if self.command != "GET":      # journal gets writes only, not every poll
            sys.stderr.write("%s %s\n" % (self.client_address[0], fmt % args))

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status >= 400:
            # The request body may not have been read; don't reuse the connection.
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def allowed(self):
        """Home network only: private client address and a local Host name."""
        host = host_only(self.headers.get("Host") or "")
        if private_ip(self.client_address[0]) and (host in HOSTNAMES or private_ip(host)):
            return True
        self.send_json({"error": "This tracker only answers on the home network."}, 403)
        return False

    def body(self):
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            self.send_json({"error": "Content-Type must be application/json"}, 415)
            return None
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self.send_json({"error": "request too large"}, 413)
            return None
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            self.send_json({"error": "invalid JSON"}, 400)
            return None

    def job_id(self, path, suffix="", kind="jobs"):
        m = re.match(r"^/api/" + kind + r"/([^/]+)" + suffix + "$", path)
        return m.group(1) if m and ID_RE.match(m.group(1)) else None

    def send_page(self, name):
        with open(os.path.join(STATIC, name), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def set_deleted(self, table, item_id, deleted):
        """Soft-delete or restore one row; returns True if it changed."""
        with lock:
            db = connect()
            n = db.execute(f"UPDATE {table} SET deleted=?, updated=? WHERE id=? AND deleted=?",
                           (int(deleted), time.time(), item_id, int(not deleted))).rowcount
            if n and table == "jobs":
                log(db, item_id, "deleted" if deleted else "restored")
            db.commit()
            db.close()
        return bool(n)

    def do_GET(self):
        if not self.allowed():
            return
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self.send_page("index.html")
        elif url.path in ("/log", "/log.html"):
            self.send_page("log.html")
        elif url.path == "/api/log":
            db = connect()
            out = []
            for n in db.execute("""SELECT n.*, j.company AS job_company, j.role AS job_role, j.deleted AS job_deleted
                                   FROM notes n LEFT JOIN jobs j ON n.link_type = 'job' AND j.id = n.job_id
                                   WHERE n.deleted = 0 ORDER BY n.ts DESC, n.created DESC"""):
                is_job = n["link_type"] == "job"
                out.append({"id": n["id"], "ts": n["ts"], "text": n["text"], "link_type": n["link_type"],
                            "company": (n["job_company"] or "") if is_job else n["company"],
                            "role": (n["job_role"] or "") if is_job else "",
                            "job_id": n["job_id"] if is_job else "",
                            "job_deleted": bool(n["job_deleted"]) if is_job else False})
            db.close()
            self.send_json({"entries": out})
        elif url.path == "/api/jobs":
            db = connect()
            rows = [row_dict(r) for r in db.execute("SELECT * FROM jobs WHERE deleted=0 ORDER BY created, id")]
            db.close()
            self.send_json({"rows": rows})
        elif url.path == "/api/history":
            try:
                limit = max(1, min(500, int(parse_qs(url.query).get("limit", ["50"])[0])))
            except ValueError:
                limit = 50
            major = parse_qs(url.query).get("major", ["0"])[0] == "1"
            where = "WHERE h.action != 'imported' AND (h.action != 'changed' OR h.field = 'status')" if major else ""
            db = connect()
            out = []
            for h in db.execute(f"""SELECT h.*, j.company, j.role, j.deleted FROM history h
                                    LEFT JOIN jobs j ON j.id = h.job_id {where} ORDER BY h.n DESC LIMIT ?""", (limit,)):
                out.append({"ts": h["ts"], "job_id": h["job_id"], "action": h["action"],
                            "field": LABELS.get(h["field"], h["field"]), "old": h["old"], "new": h["new"],
                            "company": h["company"] or "", "role": h["role"] or "",
                            "deleted": bool(h["deleted"])})
            db.close()
            self.send_json({"history": out})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_PUT(self):
        if not self.allowed():
            return
        nid = self.job_id(urlparse(self.path).path, kind="log")
        if nid:
            return self.put_note(nid)
        jid = self.job_id(urlparse(self.path).path)
        if not jid:
            return self.send_json({"error": "not found"}, 404)
        data = self.body()
        if data is None:
            return
        r = clean(data)
        r["id"] = jid
        with lock:
            db = connect()
            upsert(db, r)
            db.commit()
            db.close()
        self.send_json({"ok": True})

    def put_note(self, nid):
        data = self.body()
        if data is None:
            return
        if not isinstance(data, dict):
            return self.send_json({"error": "expected an object"}, 400)
        text = str(data.get("text") or "").strip()[:MAX_FIELD]
        link_type = str(data.get("link_type") or "")
        company = str(data.get("company") or "").strip()[:200]
        job = str(data.get("job_id") or "")
        now = time.time()
        try:
            ts = float(data.get("ts") or now)
        except (TypeError, ValueError):
            ts = now
        if not 946684800 <= ts <= now + 86400:     # between 2000 and tomorrow
            ts = now
        if not text:
            return self.send_json({"error": "the entry has no text"}, 400)
        if link_type not in ("", "company", "job"):
            return self.send_json({"error": "unknown link type"}, 400)
        if link_type == "company" and not company:
            return self.send_json({"error": "choose or type a company"}, 400)
        with lock:
            db = connect()
            if link_type == "job" and not (ID_RE.match(job) and
                                           db.execute("SELECT 1 FROM jobs WHERE id=?", (job,)).fetchone()):
                db.close()
                return self.send_json({"error": "that application was not found"}, 400)
            company = company if link_type == "company" else ""
            job = job if link_type == "job" else ""
            if db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone():
                db.execute("UPDATE notes SET ts=?, text=?, link_type=?, company=?, job_id=?, updated=?, deleted=0 WHERE id=?",
                           (ts, text, link_type, company, job, now, nid))
            else:
                db.execute("INSERT INTO notes (id, ts, text, link_type, company, job_id, created, updated) VALUES (?,?,?,?,?,?,?,?)",
                           (nid, ts, text, link_type, company, job, now, now))
            db.commit()
            db.close()
        self.send_json({"ok": True})

    def do_DELETE(self):
        if not self.allowed():
            return
        nid = self.job_id(urlparse(self.path).path, kind="log")
        if nid:
            self.set_deleted("notes", nid, True)
            return self.send_json({"ok": True})
        jid = self.job_id(urlparse(self.path).path)
        if not jid:
            return self.send_json({"error": "not found"}, 404)
        self.set_deleted("jobs", jid, True)
        self.send_json({"ok": True})

    def do_POST(self):
        if not self.allowed():
            return
        path = urlparse(self.path).path
        nid = self.job_id(path, "/restore", kind="log")
        jid = self.job_id(path, "/restore")
        if nid or jid:
            if self.body() is None:
                return
            self.set_deleted("notes" if nid else "jobs", nid or jid, False)
            return self.send_json({"ok": True})
        if path == "/api/import":
            data = self.body()
            if data is None:
                return
            rows = data if isinstance(data, list) else data.get("rows") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                return self.send_json({"error": "no rows found"}, 400)
            rows = [clean(r) for r in rows]
            if any(not ID_RE.match(r["id"]) for r in rows) or len({r["id"] for r in rows}) != len(rows):
                return self.send_json({"error": "every row needs its own id"}, 400)
            keep = {r["id"] for r in rows}
            with lock:
                db = connect()
                for old in db.execute("SELECT id FROM jobs WHERE deleted=0").fetchall():
                    if old["id"] not in keep:
                        db.execute("UPDATE jobs SET deleted=1, updated=? WHERE id=?", (time.time(), old["id"]))
                        log(db, old["id"], "deleted")
                for r in rows:
                    upsert(db, r)
                db.commit()
                db.close()
            return self.send_json({"ok": True, "rows": len(rows)})
        self.send_json({"error": "not found"}, 404)


def main():
    init_db()
    backup_daily()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"job tracker listening on port {PORT}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
