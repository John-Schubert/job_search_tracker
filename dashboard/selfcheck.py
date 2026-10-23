#!/usr/bin/env python3
"""Checks to run before each commit.

- No em dash anywhere in the dashboard's files.
- The tracker database opens read-only and a write attempt is refused.

    python3 selfcheck.py [path/to/tracker.db]
"""
import os
import sqlite3
import sys

import reader

BASE = os.path.dirname(os.path.abspath(__file__))
EM_DASH = chr(0x2014)


def files():
    for root, dirs, names in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in ("data", "__pycache__")]
        for n in names:
            if n.endswith((".py", ".html", ".css", ".js", ".json", ".md", ".example")):
                yield os.path.join(root, n)


def main():
    ok = True
    for path in files():
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if EM_DASH in line:
                    print(f"em dash: {os.path.relpath(path, BASE)}:{i}")
                    ok = False

    db_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "..", "data", "tracker.db")
    db = reader.connect(db_path)
    try:
        db.execute("CREATE TABLE dashboard_write_test (x)")
        print("tracker database accepted a write")
        ok = False
    except sqlite3.OperationalError:
        pass
    finally:
        db.close()
    t = reader.Tracker(db_path)
    print(f"read {len(t.jobs)} tracker rows, read-only")

    print("ok" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
