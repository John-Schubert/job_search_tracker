#!/usr/bin/env python3
"""Pre-commit hook: refuse to commit personal data, secrets or network details.

Checks the staged file names, then every added line of the staged changes.
Installed into .git/hooks by githooks/install.py. The rules are in CLAUDE.md.
"""
import re
import subprocess
import sys

BAD_PATH = re.compile(r"(^|/)(data|original|screenshots|node_modules|venv|\.venv)/"
                      r"|(^|/)\.env|(^|/)known_hosts$|(^|/)CLAUDE\.local\.md$"
                      r"|\.(db|sqlite|sqlite3|csv|log|pem|key|bak|old|orig|service)$|~$")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
SAFE_EMAIL = re.compile(r"(^|\.)example\.(com|org|net)$")
PHONE = re.compile(r"(?<![\d.])\(?(\d{3})\)?[-. ]\d{3}[-. ]\d{4}(?![\d.])")
PRIVATE_IP = re.compile(r"\b(10(\.\d{1,3}){3}|192\.168(\.\d{1,3}){2}|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2})\b")
LOCAL_HOST = re.compile(r"\b([a-z0-9-]+\.)+local\b(?![.\w])", re.I)
WORDS = re.compile(r"password|secret|token|api[_-]?key|smtp", re.I)
WORDS_EXEMPT = {"CLAUDE.md", "githooks/pre-commit.py"}    # these have to name the words


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def main():
    problems = []
    for path in git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines():
        if BAD_PATH.search(path):
            problems.append(f"{path}: this kind of file is never committed")

    path = None
    diff = git("diff", "--cached", "-U0", "--no-color", "--diff-filter=ACMR")
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            continue
        if not path or not line.startswith("+"):
            continue
        text = line[1:]
        found = []
        found += [f"email {m.group(0)}" for m in EMAIL.finditer(text) if not SAFE_EMAIL.search(m.group(1))]
        found += [f"phone number {m.group(0)}" for m in PHONE.finditer(text) if m.group(1) not in ("000", "555")]
        found += [f"private IP {m.group(0)}" for m in PRIVATE_IP.finditer(text)]
        found += [f"hostname {m.group(0)}" for m in LOCAL_HOST.finditer(text)
                  if not m.group(0).lower().endswith("example.local")]
        if path not in WORDS_EXEMPT:
            found += [f"the word '{m.group(0)}'" for m in WORDS.finditer(text)]
        problems += [f"{path}: {f}" for f in found]

    if problems:
        print("pre-commit: refusing to commit. See CLAUDE.md section 1.", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
