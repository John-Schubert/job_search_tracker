#!/usr/bin/env python3
"""Pre-push hook: show every file in the commits about to be pushed and the
repo's visibility. On a public (or unknown) repo, anything that is not plain
source code or docs waits for an explicit OK: type yes at the prompt, or run
the push as PUSH_OK=1 git push ...

Installed into .git/hooks by githooks/install.py. The rules are in CLAUDE.md.
"""
import os
import subprocess
import sys

ZERO = "0" * 40
SAFE_SUFFIXES = (".py", ".html", ".css", ".js", ".md")
SAFE_NAMES = {"LICENSE", ".gitignore"}


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def visibility():
    try:
        out = subprocess.run(["gh", "repo", "view", "--json", "visibility", "-q", ".visibility"],
                             capture_output=True, text=True, timeout=20)
        return out.stdout.strip().upper() or "UNKNOWN"
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN"


def main():
    remote = sys.argv[1] if len(sys.argv) > 1 else "?"
    commits, files, deletes = [], set(), []
    for line in sys.stdin:
        local_ref, local_sha, remote_ref, remote_sha = line.split()
        if local_sha == ZERO:
            deletes.append(remote_ref)
            continue
        rng = [local_sha, "--not", f"--remotes={remote}"] if remote_sha == ZERO else [f"{remote_sha}..{local_sha}"]
        commits += git("log", "--format=%h %s", *rng).splitlines()
        files |= set(git("log", "--name-only", "--format=", *rng).split())

    vis = visibility()
    print(f"pre-push: {remote} is {vis}", file=sys.stderr)
    print("commits:", file=sys.stderr)
    for c in commits:
        print("  " + c, file=sys.stderr)
    print("files:", file=sys.stderr)
    for f in sorted(files):
        print("  " + f, file=sys.stderr)
    for d in deletes:
        print("  deletes remote branch " + d, file=sys.stderr)

    other = [f for f in files if not (f.endswith(SAFE_SUFFIXES) or f.rsplit("/", 1)[-1] in SAFE_NAMES)]
    if vis == "PRIVATE" or (not other and not deletes):
        return
    print(f"Not plain source or docs: {', '.join(sorted(other)) or 'branch delete'}", file=sys.stderr)
    if os.environ.get("PUSH_OK") == "1":
        print("PUSH_OK=1 given, pushing.", file=sys.stderr)
        return
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write("Push this to a public repo? Type yes to continue: ")
            tty.flush()
            if tty.readline().strip().lower() == "yes":
                return
    except OSError:
        pass
    print("pre-push: stopped. Push again with PUSH_OK=1 once the list is checked.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
