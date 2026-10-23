#!/usr/bin/env python3
"""Job search daily dashboard: one calm page on top of the tracker and log.

Reads the tracker's database read-only (reader.py) and keeps its own small
database for check-ins, counters and the rest (store.py). The page is
rendered here as plain HTML; static/dashboard.js only sends form data.
Python standard library only. Same home-network rules as the tracker:
private clients, a local Host header, JSON-only writes.

  GET  /                  the page (?all=1 shows every panel after the
                          done-line is met or the day is closed; ?review=1
                          shows the weekly review on any day)
  POST /api/count         {"kind": "networking"|"followups", "delta": 1|-1}
  POST /api/checkin       {"energy": 1-5, "activity": "...", "note": "..."}
  POST /api/resume        {"job_id": tracker row id, "version": "v2" or "" to clear}
  POST /api/lesson        {"job_id", "reason": "rejected"|"ghosted", "text"}
  POST /api/review        {"moved", "worked", "change", "proud"} for this week
  POST /api/win           {"text"}
  POST /api/win/delete    {"id"}

Set DASHBOARD_NOW=YYYY-MM-DDTHH:MM to render as if it were that time (tests
and the demo).
"""
import datetime
import html
import ipaddress
import json
import os
import random
import re
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import reader
import store

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
PORT = int(os.environ.get("PORT", "8050"))
MAX_BODY = 20_000
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

HOSTNAMES = {"localhost", socket.gethostname().lower(), socket.gethostname().lower() + ".local"}
HOSTNAMES |= {h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()}


def now():
    fixed = os.environ.get("DASHBOARD_NOW")
    return datetime.datetime.fromisoformat(fixed) if fixed else datetime.datetime.now()


def esc(v):
    """Escape for HTML. Em dashes, which may come from tracker text, become hyphens."""
    return html.escape(str(v).replace("\u2014", "-"))


def short(d):
    return f"{d:%b} {d.day}"


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


class Page:
    """Everything one page view needs, gathered once."""

    def __init__(self, cfg, show_all=False, show_review=False):
        self.cfg = cfg
        self.show_all = show_all
        self.now = now()
        self.ts = self.now.timestamp()
        self.today = self.now.date()
        self.t = reader.Tracker(cfg["tracker_db"])
        week_ago = (self.today - datetime.timedelta(days=6)).isoformat()
        with store.Store() as s:
            self.counts = s.counts(self.today.isoformat())
            self.checkin = s.checkin(self.today.isoformat())
            self.week = {c["date"]: c for c in s.checkins(week_ago, self.today.isoformat())}
            self.recent = s.recent_checkins(14)
            self.monday = self.today - datetime.timedelta(days=self.today.weekday())
            sunday = self.monday + datetime.timedelta(days=6)
            self.week_counts = s.counts_between(self.monday.isoformat(), sunday.isoformat())
            self.week_checkins = s.checkins(self.monday.isoformat(), sunday.isoformat())
            self.review = s.review(self.monday.isoformat())
            self.wins = s.wins()
            self.tags = s.resume_tags()
            self.answered = s.lessons_answered()
            self.baseline = s.baseline()
        self.light = DAYS[self.today.weekday()] in cfg["light_days"]
        self.target = cfg["light_day_line"] if self.light else cfg["done_line"]
        self.progress = {"applications": self.t.applied_between(self.today, self.today), **self.counts}
        self.met = all(self.progress[k] >= n for k, n in self.target.items())
        hh, mm = (int(x) for x in cfg["hard_stop"].split(":"))
        self.after_stop = self.now.time() >= datetime.time(hh, mm)
        # A call today or tomorrow keeps the search open in the evening.
        self.calls_soon = self.t.upcoming_calls(self.today, 1)
        self.closed = self.after_stop and not self.calls_soon
        self.review_day = DAYS[self.today.weekday()] == cfg["review_day"]
        self.review_asked = show_review
        self.show_review = show_review or self.review_day

    def link(self, j):
        return (f'<a href="{esc(self.cfg["tracker_url"])}" title="Opens the tracker. Search for this company there.">'
                f'{esc(j["company"])}</a>, {esc(j["role"])}')

    # 1. Today
    def today_panel(self):
        labels = {"applications": "Targeted applications", "networking": "Networking touches",
                  "followups": "Follow-ups on warm threads"}
        rows = []
        for kind, label in labels.items():
            goal, n = self.target.get(kind, 0), self.progress[kind]
            if goal == 0 and n == 0:
                continue
            if kind == "applications":
                buttons = '<span class="muted" title="Counted from the applied dates in the tracker">tracker</span>'
            else:
                body = esc(json.dumps({"kind": kind, "delta": 1}))
                undo = esc(json.dumps({"kind": kind, "delta": -1}))
                buttons = (f'<button data-api="/api/count" data-body="{body}">+1</button>'
                           + (f'<button class="small" data-api="/api/count" data-body="{undo}" title="Take one back">undo</button>'
                              if n else ""))
            met = " met" if n >= goal else ""
            rows.append(f'<div class="goal{met}"><span class="label">{label}</span>'
                        f'<span class="n">{n} of {goal}</span>{buttons}</div>')
        # Recovery is part of the plan, so it sits with the work counts.
        activity = (self.checkin or {}).get("activity", "")
        rows.append(f'<div class="goal{" met" if activity else ""}"><span class="label">Non-job activity</span>'
                    f'<span class="n">{esc(activity) if activity else "<span class=muted>not set yet</span>"}</span></div>')
        title = "Today, a light day" if self.light else "Today"
        out = [f'<section><h2>{title} <span class="sub">{self.today:%A, %B} {self.today.day}</span></h2>{"".join(rows)}']
        if self.progress["applications"] > self.cfg["volume_note_over"]:
            out.append('<p class="note muted">A lot of applications went out today. Volume is not the goal; '
                       'a few targeted ones go further.</p>')
        if self.calls_soon and self.after_stop:
            out.append('<p class="note muted">Open late because of a call coming up.</p>')
        out.append("</section>")
        return "".join(out)

    def resting_panel(self):
        if self.closed:
            msg = "Search is closed for the day."
            sub = "Anything left can wait until tomorrow."
        else:
            msg = "Done for today."
            sub = "The done-line is met. The rest of the day is yours."
        review = ('<div class="more">It is weekly review day. <a href="?review=1">Open the review</a></div>'
                  if self.review_day and not self.review and not self.review_asked else "")
        return (f'<section class="calm"><h2>{msg}</h2><div>{sub}</div>{review}'
                f'<div class="more"><a href="?all=1">Show everything anyway</a></div></section>')

    # 2. Due now
    def due_panel(self):
        items, listed = [], set()
        # Each application is listed once: a call first, then a follow-up, then quiet.
        for j in self.t.upcoming_calls(self.today, self.cfg["upcoming_days"]):
            listed.add(j["id"])
            when = "today" if j["followup_date"] == self.today else short(j["followup_date"])
            items.append(f'<li>{esc(j["status"])}: {self.link(j)} <span class="muted">({when})</span></li>')
        for j in self.t.followups_due(self.today):
            if j["id"] in listed:
                continue
            listed.add(j["id"])
            when = "today" if j["followup_date"] == self.today else f'set for {short(j["followup_date"])}'
            items.append(f'<li>Follow up: {self.link(j)} <span class="muted">({when})</span></li>')
        quiet = [j for j in self.t.quiet(self.ts, self.cfg["stale_days"], self.cfg["ghosted_days"])
                 if j["id"] not in listed]
        for j in quiet[:4]:
            since = short(reader.day(j["last_activity"]))
            items.append(f'<li>Quiet since {since}: {self.link(j)}</li>')
        more = (f'<div class="more muted">{plural(len(quiet) - 4, "more quiet application")} in the '
                f'<a href="{esc(self.cfg["tracker_url"])}">tracker</a>.</div>' if len(quiet) > 4 else "")
        body = f'<ul>{"".join(items)}</ul>{more}' if items else '<div class="muted">Nothing is due right now.</div>'
        return f'<section><h2>Due now</h2>{body}</section>'

    # 4. Energy and recovery check-in
    def checkin_panel(self):
        c = self.checkin or {}
        scale = "".join(f'<label title="{n}"><input type="radio" name="energy" value="{n}"'
                        f'{" checked" if c.get("energy") == n else ""}><span>{n}</span></label>' for n in range(1, 6))
        acts = "".join(f'<option value="{esc(a)}">' for a in self.cfg["activities"])
        saved = '<span class="muted">Saved for today. Change it any time.</span>' if c else ""
        return f"""<section><h2>Energy and recovery</h2>
<form data-api="/api/checkin">
  <div class="row"><span class="muted">Energy, 1 low to 5 high</span><div class="scale">{scale}</div></div>
  <div class="row"><input type="text" name="activity" list="acts" maxlength="200" value="{esc(c.get("activity", ""))}"
    placeholder="Non-job activity, done or planned"></div>
  <datalist id="acts">{acts}</datalist>
  <div class="row"><input type="text" name="note" maxlength="300" value="{esc(c.get("note", ""))}" placeholder="Note (optional)"></div>
  <div class="row"><button class="primary">Save</button>{saved}</div>
</form>
{self.trend()}{self.burnout_note()}</section>"""

    def trend(self):
        if not self.week:
            return '<div class="sub note">The 7-day trend starts with the first check-in.</div>'
        days = []
        for i in range(6, -1, -1):
            d = self.today - datetime.timedelta(days=i)
            c = self.week.get(d.isoformat())
            if c:
                tip = f'{d:%a}: energy {c["energy"]}' + (f', {c["activity"]}' if c["activity"] else "")
                bar = f'<div class="bar" style="height:{c["energy"] * 11}px"></div>'
                dot = "&bull;" if c["activity"] else ""
            else:
                tip, bar, dot = f"{d:%a}: no check-in", "", ""
            days.append(f'<div class="day" title="{esc(tip)}">{bar}<span class="dot">{dot}</span>{d:%a}</div>')
        return (f'<div class="trend">{"".join(days)}</div>'
                '<div class="sub">Last 7 days. Bar height is energy; a dot means a non-job activity.</div>')

    # 5. Burnout signal: one quiet note at most
    def burnout_note(self):
        recent = self.recent    # newest first, up to 14 check-ins
        start = (self.today - datetime.timedelta(days=13)).isoformat()
        window = [c for c in recent if c["date"] >= start]
        if (len(window) >= 10 and all(c["energy"] <= 2 for c in window)
                and window[-1]["date"] <= (self.today - datetime.timedelta(days=12)).isoformat()):
            msg = ("Energy has stayed at 1 or 2 for about two weeks. Burnout is common in long searches, "
                   "and support helps. It may be worth talking with your doctor or a counselor.")
        elif len(recent) >= 5 and sum(c["energy"] for c in recent[:5]) / 5 <= 2:
            msg = "Energy has been low for a few days. A lighter day could help: one follow-up, then time away from the screen."
        elif (len(recent) >= 3 and not any(c["activity"] for c in recent[:3])
              and datetime.date.fromisoformat(recent[0]["date"]) - datetime.date.fromisoformat(recent[2]["date"])
              == datetime.timedelta(days=2)):
            msg = "No non-job activity for three days. A lighter day with a walk or some station time might be good."
        else:
            return ""
        return f'<p class="note muted">{msg}</p>'

    # 3. Pipeline snapshot
    def rates_table(self):
        """Response rate by resume version, over every application sent."""
        rows = {}
        for j in self.t.sent():
            r = rows.setdefault(self.tags.get(j["id"], ""), [0, 0])
            r[0] += 1
            r[1] += self.t.responded(j["id"])
        if not rows:
            return '<div class="muted">No applications sent yet.</div>'
        trs = ""
        for v in sorted(k for k in rows if k) + ([""] if "" in rows else []):
            n, r = rows[v]
            name = esc(v) if v else '<span class="muted">not tagged</span>'
            trs += f'<tr><td>{name}</td><td class="num">{n}</td><td class="num">{r}</td><td class="num">{round(100 * r / n)}%</td></tr>'
        return ('<table><tr><th>Resume</th><th class="num">Sent</th><th class="num">Responses</th>'
                f'<th class="num">Rate</th></tr>{trs}</table>'
                '<div class="sub">A response is a recruiter screen or any later stage, at any point.</div>')

    def pipeline_panel(self):
        counts = self.t.status_counts()
        chips = " ".join(f'<span class="tag">{esc(st)} {counts[st]}</span>' for st in reader.STATUSES if counts.get(st))
        last_monday = self.monday - datetime.timedelta(days=7)
        this_week = self.t.applied_between(self.monday, self.today)
        last_week = self.t.applied_between(last_monday, self.monday - datetime.timedelta(days=1))
        since = self.today - datetime.timedelta(days=14)
        recent = sorted((j for j in self.t.sent() if j["applied_date"] >= since),
                        key=lambda j: j["applied_date"], reverse=True)
        tag_rows = []
        for j in recent:
            cur = self.tags.get(j["id"], "")
            versions = list(self.cfg["resume_versions"]) + ([cur] if cur and cur not in self.cfg["resume_versions"] else [])
            opts = '<option value="">untagged</option>' + "".join(
                f'<option{" selected" if v == cur else ""}>{esc(v)}</option>' for v in versions)
            body = esc(json.dumps({"job_id": j["id"]}))
            tag_rows.append(f'<li class="goal"><span class="label">{esc(j["company"])}, {esc(j["role"])} '
                            f'<span class="muted">({short(j["applied_date"])})</span></span>'
                            f'<select name="version" data-api="/api/resume" data-body="{body}">{opts}</select></li>')
        untagged = sum(1 for j in recent if j["id"] not in self.tags)
        tagging = (f'<details{" open" if untagged else ""}><summary>Resume version for the last two weeks of '
                   f'applications ({untagged} untagged)</summary><ul>{"".join(tag_rows)}</ul></details>'
                   if recent else "")
        return (f'<section><h2>Pipeline</h2><div>{chips}</div>'
                f'<p class="note">Applications this week: {this_week}. Last week: {last_week}.</p>'
                f'{self.rates_table()}{tagging}</section>')

    # 6. Weekly review
    def review_panel(self):
        r = self.review or {}
        apps = self.t.applied_between(self.monday, self.today)
        net, fol = self.week_counts["networking"], self.week_counts["followups"]
        energies = [c["energy"] for c in self.week_checkins]
        avg = f"average energy {sum(energies) / len(energies):.1f}" if energies else "no check-ins yet"
        stats = (f'This week: {plural(apps, "application")}, {net} networking touch{"" if net == 1 else "es"}, '
                 f'{plural(fol, "follow-up")}, {avg}.')
        # The same win all week, so a reload does not shuffle it.
        win = random.Random(self.monday.isoformat()).choice(self.wins) if self.wins else None
        win_line = f'<p class="note">A win to remember: {esc(win["text"])}</p>' if win else ""
        prompts = [("moved", "What moved forward this week?"),
                   ("worked", "Which resume version or approach got responses?"),
                   ("change", "What is one thing I will drop or do differently next week?"),
                   ("proud", "What am I proud of this week, even if it was small?")]
        fields = "".join(f'<label class="q">{q}<textarea name="{k}" maxlength="1000">{esc(r.get(k, ""))}</textarea></label>'
                         for k, q in prompts)
        saved = '<span class="muted">Saved. You can change it until the week ends.</span>' if r else ""
        return (f'<section><h2>Weekly review <span class="sub">week of {short(self.monday)}, about 10 minutes</span></h2>'
                f'<p class="note">{stats}</p>{self.rates_table()}{win_line}'
                f'<form data-api="/api/review">{fields}<div class="row"><button class="primary">Save</button>{saved}</div></form>'
                '</section>')

    # 7. Wins log
    def wins_panel(self):
        def item(w):
            body = esc(json.dumps({"id": w["id"]}))
            return (f'<li>{esc(w["text"])} <span class="muted">{short(reader.day(w["ts"]))}</span> '
                    f'<button type="button" class="small" data-api="/api/win/delete" data-body="{body}">remove</button></li>')
        latest = "".join(item(w) for w in self.wins[:5])
        older = (f'<details><summary>{plural(len(self.wins) - 5, "older win")}</summary>'
                 f'<ul>{"".join(item(w) for w in self.wins[5:])}</ul></details>' if len(self.wins) > 5 else "")
        return (f'<section><h2>Wins</h2><ul>{latest}</ul>{older}'
                '<form data-api="/api/win"><div class="row"><input type="text" name="text" maxlength="300" style="flex:1;width:auto" '
                'placeholder="A recruiter reply, an interview, a kind word about your background"><button>Add</button></div></form>'
                '</section>')

    # 8. One question when an application closes
    def lesson_panel(self):
        ask = [(ts, j, "rejected") for j, ts in self.t.rejected_since(self.baseline)
               if (j["id"], "rejected") not in self.answered]
        ask += [(ts, j, "ghosted") for j, ts in self.t.ghosted(self.ts, self.cfg["ghosted_days"])
                if ts > self.baseline and (j["id"], "ghosted") not in self.answered]
        if not ask:
            return ""
        ts, j, reason = min(ask, key=lambda a: a[0])
        if reason == "rejected":
            what = "was marked Rejected in the tracker."
        else:
            what = (f'has had no reply for {self.cfg["ghosted_days"]} days, so it may be ghosted. '
                    "The tracker is not changed.")
        skip = esc(json.dumps({"job_id": j["id"], "reason": reason, "text": ""}))
        more = f'<div class="sub">{len(ask) - 1} more after this one.</div>' if len(ask) > 1 else ""
        return (f'<section><h2>One question</h2><div>{self.link(j)} {what}</div>'
                f'<form data-api="/api/lesson"><input type="hidden" name="job_id" value="{esc(j["id"])}">'
                f'<input type="hidden" name="reason" value="{reason}">'
                '<div class="row"><input type="text" name="text" maxlength="300" style="flex:1;width:auto" '
                'placeholder="One line on what you learned"><button class="primary">Save</button>'
                f'<button type="button" data-api="/api/lesson" data-body="{skip}">Nothing, move on</button></div></form>'
                f'{more}</section>')

    def render(self):
        resting = (self.met or self.closed) and not self.show_all
        if resting:
            panels = [self.resting_panel(), self.checkin_panel()]
            if self.review_asked:
                panels.append(self.review_panel())
        else:
            panels = [self.today_panel(), self.due_panel(), self.lesson_panel(), self.pipeline_panel(),
                      self.checkin_panel()]
            if self.show_review:
                panels.append(self.review_panel())
            panels.append(self.wins_panel())
            if not self.show_review:
                panels.append('<div class="sub"><a href="?all=1&amp;review=1">Weekly review</a></div>')
        url = esc(self.cfg["tracker_url"])
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Search Today</title>
<link rel="stylesheet" href="/static/dashboard.css">
<script src="/static/dashboard.js" defer></script>
</head>
<body>
<main>
<header>
  <h1>Job Search Today</h1>
  <nav class="nav"><a href="/" class="cur">Today</a><a href="{url}">Tracker</a><a href="{url}log">Log</a></nav>
</header>
<div id="err" class="note" hidden></div>
{"".join(panels)}
</main>
</body>
</html>
"""


def private_ip(text):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


def host_only(header):
    """Host header without its port: 'host.example.local:8050' -> 'host.example.local', '[::1]:8050' -> '::1'."""
    host = header.strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class Handler(BaseHTTPRequestHandler):
    server_version = "JobDashboard/1.0"
    protocol_version = "HTTP/1.1"
    cfg = None

    def log_message(self, fmt, *args):
        if self.command != "GET":
            sys.stderr.write("%s %s\n" % (self.client_address[0], fmt % args))

    def send(self, body, ctype, status=200, cache="no-store"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        if status >= 400:
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status=200):
        self.send(json.dumps(obj), "application/json", status)

    def allowed(self):
        host = host_only(self.headers.get("Host") or "")
        if private_ip(self.client_address[0]) and (host in HOSTNAMES or private_ip(host)):
            return True
        self.send_json({"error": "This dashboard only answers on the home network."}, 403)
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
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self.send_json({"error": "expected a JSON object"}, 400)
            return None
        return data

    def do_GET(self):
        if not self.allowed():
            return
        url = urlparse(self.path)
        if url.path == "/":
            try:
                q = parse_qs(url.query)
                page = Page(self.cfg, q.get("all") == ["1"], q.get("review") == ["1"]).render()
            except Exception as e:    # e.g. the tracker database is missing
                print(f"page failed: {e!r}", file=sys.stderr)
                return self.send("<p>The dashboard could not read the tracker just now. "
                                 "Try again in a moment.</p>", "text/html; charset=utf-8", 503)
            self.send(page, "text/html; charset=utf-8")
        elif url.path in ("/static/dashboard.css", "/static/dashboard.js"):
            name = url.path.rsplit("/", 1)[1]
            with open(os.path.join(STATIC, name), "rb") as f:
                ctype = "text/css" if name.endswith(".css") else "text/javascript"
                self.send(f.read(), ctype + "; charset=utf-8", cache="no-cache")
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.allowed():
            return
        path = urlparse(self.path).path
        data = self.body()
        if data is None:
            return
        today = now().date()
        monday = (today - datetime.timedelta(days=today.weekday())).isoformat()
        today = today.isoformat()
        job = str(data.get("job_id") or "")
        if path in ("/api/resume", "/api/lesson") and (
                not JOB_ID.match(job) or job not in reader.Tracker(self.cfg["tracker_db"]).by_id):
            return self.send_json({"error": "That application was not found in the tracker."}, 400)
        try:
            with store.Store() as s:
                if path == "/api/count":
                    s.bump(today, data.get("kind"), data.get("delta"))
                elif path == "/api/checkin":
                    energy = str(data.get("energy") or "")
                    s.save_checkin(today, int(energy) if energy.isdigit() else 0,
                                   data.get("activity"), data.get("note"))
                elif path == "/api/resume":
                    s.tag_resume(job, data.get("version"))
                elif path == "/api/lesson":
                    s.save_lesson(job, data.get("reason"), data.get("text"))
                elif path == "/api/review":
                    s.save_review(monday, data.get("moved"), data.get("worked"), data.get("change"), data.get("proud"))
                elif path == "/api/win":
                    s.add_win(data.get("text"))
                elif path == "/api/win/delete":
                    s.set_win_deleted(int(data.get("id") or 0), True)
                else:
                    return self.send_json({"error": "not found"}, 404)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)
        self.send_json({"ok": True})


def main():
    Handler.cfg = store.load_config()
    store.init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"job dashboard listening on port {PORT}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
