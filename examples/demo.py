#!/usr/bin/env python3
"""Run a throwaway copy of the tracker filled with fictional data.

For trying it out and for taking screenshots without touching a real
database. Copies the server and pages to a temporary folder, loads
seed.example.json, adds a few sample log entries and a status change, and
serves on port 8767 (or $PORT) until interrupted.

    python3 examples/demo.py
"""
import json
import os
import shutil
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


def main():
    tmp = tempfile.mkdtemp(prefix="job-tracker-demo-")
    shutil.copy(os.path.join(ROOT, "server.py"), tmp)
    shutil.copytree(os.path.join(ROOT, "static"), os.path.join(tmp, "static"))
    os.makedirs(os.path.join(tmp, "data"))
    shutil.copy(os.path.join(HERE, "seed.example.json"), os.path.join(tmp, "data", "seed.json"))
    server = subprocess.Popen([sys.executable, "server.py"], cwd=tmp, env=dict(os.environ, PORT=PORT))
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

        print("Demo with fictional data: http://localhost:%s/   (Ctrl+C to stop)" % PORT, flush=True)
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.terminate()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
