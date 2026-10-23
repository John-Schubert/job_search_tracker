#!/usr/bin/env python3
"""Job search daily dashboard: one calm page on top of the tracker and log.

Reads the tracker's database read-only (reader.py) and keeps its own small
database for check-ins, counters and the rest (store.py). The page is
rendered here as plain HTML; static/dashboard.js only sends form data.
Python standard library only. Same home-network rules as the tracker:
private clients, a local Host header, JSON-only writes.

  GET  /                  the page (?all=1 shows every panel after the
                          done-line is met or the day is closed)
  POST /api/count         {"kind": "networking"|"followups", "delta": 1|-1}

Set DASHBOARD_NOW=YYYY-MM-DDTHH:MM to render as if it were that time (tests
and the demo).
"""
import datetime
import html
import ipaddress
import json
import os
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

    def __init__(self, cfg, show_all=False):
        self.cfg = cfg
        self.show_all = show_all
        self.now = now()
        self.ts = self.now.timestamp()
        self.today = self.now.date()
        self.t = reader.Tracker(cfg["tracker_db"])
        with store.Store() as s:
            self.counts = s.counts(self.today.isoformat())
        self.light = DAYS[self.today.weekday()] in cfg["light_days"]
        self.target = cfg["light_day_line"] if self.light else cfg["done_line"]
        self.progress = {"applications": self.t.applied_between(self.today, self.today), **self.counts}
        self.met = all(self.progress[k] >= n for k, n in self.target.items())
        hh, mm = (int(x) for x in cfg["hard_stop"].split(":"))
        # A call today or tomorrow keeps the search open in the evening.
        self.calls_soon = self.t.upcoming_calls(self.today, 1)
        self.closed = self.now.time() >= datetime.time(hh, mm) and not self.calls_soon

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
        title = "Today, a light day" if self.light else "Today"
        out = [f'<section><h2>{title} <span class="sub">{self.today:%A, %B} {self.today.day}</span></h2>{"".join(rows)}']
        if self.progress["applications"] > self.cfg["volume_note_over"]:
            out.append('<p class="note muted">A lot of applications went out today. Volume is not the goal; '
                       'a few targeted ones go further.</p>')
        if self.calls_soon and self.now.time() >= datetime.time(*(int(x) for x in self.cfg["hard_stop"].split(":"))):
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
        return (f'<section class="calm"><h2>{msg}</h2><div>{sub}</div>'
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

    def render(self):
        resting = (self.met or self.closed) and not self.show_all
        if resting:
            panels = [self.resting_panel()]
        else:
            panels = [self.today_panel(), self.due_panel()]
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
                page = Page(self.cfg, parse_qs(url.query).get("all") == ["1"]).render()
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
        today = now().date().isoformat()
        try:
            with store.Store() as s:
                if path == "/api/count":
                    s.bump(today, data.get("kind"), data.get("delta"))
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
