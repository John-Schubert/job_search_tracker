#!/usr/bin/env python3
"""Install the pre-commit and pre-push hooks into this clone's .git/hooks.

Hooks are not pushed with the repo, so run this after every fresh clone:

    python3 githooks/install.py
"""
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    hooks = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=HERE,
                           capture_output=True, text=True, check=True).stdout.strip()
    hooks = os.path.join(HERE, hooks) if not os.path.isabs(hooks) else hooks
    os.makedirs(hooks, exist_ok=True)
    for name in ("pre-commit", "pre-push"):
        target = os.path.join(hooks, name)
        if os.path.exists(target) and not os.path.exists(target + ".before-githooks"):
            shutil.copy2(target, target + ".before-githooks")
        shutil.copy(os.path.join(HERE, name + ".py"), target)
        os.chmod(target, 0o755)
        print("installed", target)


if __name__ == "__main__":
    main()
