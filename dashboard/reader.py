"""Read-only view of the job tracker's database.

Opens tracker.db with mode=ro and PRAGMA query_only, so this module cannot
change the tracker's data even by mistake. It relies on the tracker's three
tables: jobs, history and notes (the Log).

Last activity on a row is the latest of: its applied date, its newest status
or follow-up date change, and its newest linked log note. Adding a row and
editing its other cells are bookkeeping, not activity, and jobs.updated is
not used because any save of a row moves it even when nothing changed.

    python3 reader.py [path/to/tracker.db]    prints a short summary
"""
import datetime
import sqlite3
import sys
import time
from urllib.parse import quote

# The tracker's statuses, in its own order.
STATUSES = ["Researching", "Preparing", "Applied", "Recruiter screen", "Interviewing",
            "Awaiting decision", "Offer", "Rejected", "Closed / filled", "Withdrawn"]
ENDED = {"Rejected", "Closed / filled", "Withdrawn"}
NOT_SENT = {"Researching", "Preparing"}
RESPONDED = {"Recruiter screen", "Interviewing", "Awaiting decision", "Offer"}
CALLS = {"Recruiter screen", "Interviewing", "Awaiting decision"}


def connect(path):
    db = sqlite3.connect("file:" + quote(path) + "?mode=ro", uri=True, timeout=10)
    db.execute("PRAGMA query_only = 1")
    db.row_factory = sqlite3.Row
    return db


def day(ts):
    return datetime.date.fromtimestamp(ts)


def parse_date(text):
    try:
        return datetime.date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


class Tracker:
    """One snapshot of the tracker, loaded once per page view."""

    def __init__(self, path):
        db = connect(path)
        try:
            self.jobs = [dict(r) for r in db.execute(
                "SELECT id, company, role, status, applied, followup, created FROM jobs WHERE deleted = 0")]
            last_change = dict(db.execute(
                "SELECT job_id, MAX(ts) FROM history WHERE field IN ('status', 'followup') GROUP BY job_id").fetchall())
            last_note = dict(db.execute(
                "SELECT job_id, MAX(ts) FROM notes WHERE deleted = 0 AND link_type = 'job' GROUP BY job_id").fetchall())
            # Every status a row has ever had, to count a response that later ended.
            self.statuses_seen = {}
            for h in db.execute("SELECT job_id, old, new FROM history WHERE field = 'status'"):
                self.statuses_seen.setdefault(h["job_id"], set()).update((h["old"], h["new"]))
            self.status_changes = [dict(r) for r in db.execute(
                "SELECT job_id, ts, new FROM history WHERE field = 'status' ORDER BY n")]
        finally:
            db.close()
        for j in self.jobs:
            j["applied_date"] = parse_date(j["applied"])
            j["followup_date"] = parse_date(j["followup"])
            times = [last_change.get(j["id"]), last_note.get(j["id"])]
            if j["applied_date"]:
                times.append(time.mktime(j["applied_date"].timetuple()))
            times = [t for t in times if t]
            j["last_activity"] = max(times) if times else j["created"]
            self.statuses_seen.setdefault(j["id"], set()).add(j["status"])
        self.by_id = {j["id"]: j for j in self.jobs}

    def sent(self):
        """Rows that were actually sent: an applied date and past Preparing."""
        return [j for j in self.jobs if j["applied_date"] and j["status"] not in NOT_SENT]

    def applied_between(self, start, end):
        """Count of applications with start <= applied date <= end."""
        return sum(1 for j in self.sent() if start <= j["applied_date"] <= end)

    def status_counts(self):
        counts = {}
        for j in self.jobs:
            counts[j["status"]] = counts.get(j["status"], 0) + 1
        return counts

    def responded(self, job_id):
        return bool(self.statuses_seen.get(job_id, set()) & RESPONDED)

    def followups_due(self, today):
        rows = [j for j in self.jobs if j["followup_date"] and j["followup_date"] <= today
                and j["status"] not in ENDED]
        return sorted(rows, key=lambda j: j["followup_date"])

    def upcoming_calls(self, today, days):
        """Rows in a talking stage whose follow-up date is within the next few days."""
        end = today + datetime.timedelta(days=days)
        rows = [j for j in self.jobs if j["status"] in CALLS and j["followup_date"]
                and today <= j["followup_date"] <= end]
        return sorted(rows, key=lambda j: j["followup_date"])

    def quiet(self, now, days, ghosted_days):
        """Sent, still open rows with no activity for `days`, leaving out the ones
        old enough to count as ghosted (those get their own one-time question)."""
        rows = []
        for j in self.sent():
            idle = (now - j["last_activity"]) / 86400
            if j["status"] in ENDED or idle < days:
                continue
            if j["status"] == "Applied" and idle >= ghosted_days:
                continue
            rows.append(j)
        return sorted(rows, key=lambda j: j["last_activity"])

    def ghosted(self, now, ghosted_days):
        """Still Applied with no activity for `ghosted_days`, as (row, day it went quiet)."""
        out = []
        for j in self.jobs:
            if j["status"] == "Applied" and now - j["last_activity"] >= ghosted_days * 86400:
                out.append((j, j["last_activity"] + ghosted_days * 86400))
        return out

    def rejected_since(self, since):
        """Rows still Rejected whose latest move to Rejected came after `since`."""
        latest = {}
        for h in self.status_changes:
            if h["new"] == "Rejected":
                latest[h["job_id"]] = h["ts"]
        return [(self.by_id[jid], ts) for jid, ts in latest.items()
                if ts > since and jid in self.by_id and self.by_id[jid]["status"] == "Rejected"]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "../data/tracker.db"
    t = Tracker(path)
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    now = time.time()
    print("rows:", len(t.jobs), "sent:", len(t.sent()))
    print("by status:", t.status_counts())
    print("applied today:", t.applied_between(today, today),
          "this week:", t.applied_between(monday, today),
          "last week:", t.applied_between(monday - datetime.timedelta(days=7), monday - datetime.timedelta(days=1)))
    print("responded:", sum(1 for j in t.sent() if t.responded(j["id"])))
    print("follow-ups due:", len(t.followups_due(today)), "calls in 3 days:", len(t.upcoming_calls(today, 3)))
    print("quiet 14+ days:", len(t.quiet(now, 14, 30)), "ghosted 30+ days:", len(t.ghosted(now, 30)))
    print("rejected in last 30 days:", len(t.rejected_since(now - 30 * 86400)))


if __name__ == "__main__":
    main()
