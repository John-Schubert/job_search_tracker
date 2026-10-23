#!/usr/bin/env python3
"""Run a throwaway copy of the tracker filled with fictional data.

For trying it out and for taking screenshots without touching a real
database. Copies the server, pages and the Today dashboard to a temporary
folder, loads seed.example.json, adds a few sample log entries, a status
change, and a week of made-up check-ins and wins, and serves on port 8767
(or $PORT) until interrupted. The demo clock is pinned to 10:30 AM today so
the Today page shows its working view.

    python3 examples/demo.py
"""
import datetime
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT = os.environ.get("PORT", "8767")
URL = "http://127.0.0.1:" + PORT


def call(method, path, body=None):
    req = urllib.request.Request(URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def seed_today(tmp, rows, today):
    """Fictional data for the Today page: one application sent today, a call in
    two days, a fresh rejection to ask about, a week of check-ins and two wins."""
    def day(n):
        return (today - datetime.timedelta(days=n)).isoformat()

    call("PUT", "/api/jobs/ex08", {"company": "Wayne Aerospace", "role": "Flight Test Engineer",
                                   "status": "Applied", "applied": today.isoformat(),
                                   "next": "Follow up in a week"})
    r = rows["ex01"]
    r.update(followup=(today + datetime.timedelta(days=2)).isoformat())
    call("PUT", "/api/jobs/ex01", r)
    r = rows["ex04"]
    r.update(status="Rejected")
    call("PUT", "/api/jobs/ex04", r)
    call("POST", "/api/today/count", {"kind": "networking", "delta": 1})
    call("POST", "/api/today/checkin", {"energy": "4", "activity": "Walk at lunch, then the radio station tonight"})
    call("POST", "/api/today/win", {"text": "Initech recruiter replied the same day"})
    call("POST", "/api/today/win", {"text": "Globex panel said the test background stood out"})
    for job, version in (("ex01", "v2"), ("ex02", "v1"), ("ex03", "v2"), ("ex05", "v1"), ("ex08", "v2")):
        call("POST", "/api/today/resume", {"job_id": job, "version": version})
    # Earlier days can only be written directly; the page saves today's check-in only.
    db = sqlite3.connect(os.path.join(tmp, "data", "dashboard", "dashboard.db"))
    week = [(3, "3D printing"), (2, ""), (3, "Cooking"), (4, "Drone flying"), (3, "Walking"), (2, "APRS station work")]
    for n, (energy, activity) in enumerate(week, 1):
        db.execute("INSERT OR REPLACE INTO checkins (date, energy, activity, note, updated) VALUES (?, ?, ?, '', ?)",
                   (day(n), energy, activity, time.time()))
    db.commit()
    db.close()


def main():
    # A plain kill (SIGTERM) also stops the copied server and removes the folder.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    tmp = tempfile.mkdtemp(prefix="job-tracker-demo-")
    shutil.copy(os.path.join(ROOT, "server.py"), tmp)
    shutil.copytree(os.path.join(ROOT, "static"), os.path.join(tmp, "static"))
    shutil.copytree(os.path.join(ROOT, "dashboard"), os.path.join(tmp, "dashboard"),
                    ignore=shutil.ignore_patterns("data", "__pycache__"))
    os.makedirs(os.path.join(tmp, "data"))
    shutil.copy(os.path.join(HERE, "seed.example.json"), os.path.join(tmp, "data", "seed.json"))
    today = datetime.date.today()
    env = dict(os.environ, PORT=PORT, DASHBOARD_NOW=f"{today.isoformat()}T10:30")
    env.pop("DASHBOARD_DATA", None)
    server = subprocess.Popen([sys.executable, "server.py"], cwd=tmp, env=env)
    try:
        for _ in range(50):
            try:
                rows = {r["id"]: r for r in call("GET", "/api/jobs")["rows"]}
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise SystemExit("demo server did not start")

        now = time.time()
        hour, day = 3600, 86400
        notes = [
            (now - 2 * day - 5 * hour, "Applied to two Initech roles through their careers site.", "company", "Initech", ""),
            (now - 2 * day - 2 * hour, "Final-round interview with Globex. Went well; they said one to two weeks.", "job", "", "ex02"),
            (now - 1 * day - 6 * hour, "Sent thank-you notes to the three panel interviewers.", "job", "", "ex02"),
            (now - 1 * day - 3 * hour, "Coffee with a former colleague who now works at Stark Industries. Offered to refer me.", "company", "Stark Industries", ""),
            (now - 5 * hour, "Checked the Acme careers page, nothing new this week.", "company", "Acme Robotics", ""),
            (now - 4 * hour, "Recruiter emailed to set up a first call. Replied with times for Thursday.", "job", "", "ex03"),
            (now - 2 * hour, "Updated the resume summary and exported a fresh PDF.", "", "", ""),
            (now - 1 * hour, "Reviewed system design notes ahead of the Acme panel.", "job", "", "ex01"),
        ]
        for i, (ts, text, link_type, company, job) in enumerate(notes):
            call("PUT", "/api/log/demo%02d" % i, {"ts": ts, "text": text, "link_type": link_type,
                                                    "company": company, "job_id": job})
        # A couple of edits so the change history and the Log's tracker lines have content.
        r = rows["ex03"]
        r.update(status="Recruiter screen", contact="Jo Brandt (recruiter)", next="Recruiter call Thursday 10 AM")
        call("PUT", "/api/jobs/ex03", r)
        r = rows["ex01"]
        r.update(notes=r["notes"] + " Panel confirmed for next Tuesday.")
        call("PUT", "/api/jobs/ex01", r)
        seed_today(tmp, rows, today)

        print("Demo with fictional data: http://localhost:%s/   (Ctrl+C to stop)" % PORT, flush=True)
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.terminate()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
