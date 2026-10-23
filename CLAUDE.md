# GitHub Push Rules (read before every commit and push)

These rules apply to every repo the owner works on with Claude, starting with
the job search tracker and the job search dashboard. The tracker repo is
PUBLIC. Assume anything pushed there is visible to everyone and cannot be
fully taken back.

This file contains rules only, never real values. Do not add the owner's
actual email, phone number, address, usernames, hostnames, or keys to it.
Local notes with real values go in CLAUDE.local.md, which is never committed.

## 1. Never commit or push any of the following

Personal and job-search data:
- Database files and data stores: *.db, *.sqlite, *.sqlite3, anything in a data/ folder
- CSV, JSON, or other exports of applications, contacts, logs, or check-ins
- Application records: company names tied to real applications, role titles, status, notes, dates
- Contact information about other people: recruiters, hiring managers, colleagues, referrals (names, emails, phone numbers, LinkedIn URLs)
- Energy check-ins, wellbeing notes, wins log, weekly reviews, rejection notes
- Resumes, cover letters, job descriptions, interview prep, and compensation notes
- Spec files, planning documents, and any markdown that mentions real applications or search status

Credentials and infrastructure:
- .env files and any file holding passwords, API keys, tokens, app passwords, or SMTP settings (including the ones used for the daily emails)
- Career-site logins and usernames
- SSH keys, certificates, *.pem, *.key, known_hosts
- Real config files (commit a *.example version with fake placeholder values instead)
- IP addresses, hostnames, and network layout of the Raspberry Pis and home network
- Exchange or wallet keys and anything from other projects

Noise:
- Backups (*.bak, *.old, *.orig, *~), logs (*.log), __pycache__, virtual environments, node_modules, editor folders, OS junk files

## 2. What is safe to push
- Source code, templates, and styles
- README.md, CLAUDE.md, LICENSE, requirements files
- *.example config files with obviously fake placeholders (example.com addresses, 000 phone numbers, "changeme" values)
- Tests and demo data that are clearly fake

## 3. How this is enforced
1. `.gitignore` is an allowlist: everything is ignored by default, only the safe categories above are allowed, and the "never" patterns come last so they win.
2. Never run `git add -A`, `git add .`, or `git commit -a`. Add files by explicit name only.
3. Before every commit, run `git status` and `git diff --cached --stat` and read the list. Stop if anything on it falls under section 1.
4. A local pre-commit hook (`githooks/pre-commit.py`) blocks a commit when staged changes contain: a .env file, a database or CSV file, a data/ path, a key or certificate, an email address that is not @example.com, a phone number pattern, a private IP address or a .local hostname, or strings like password, secret, token, api_key, or smtp. Python standard library only. CLAUDE.md and the hook itself are exempt from the word check, since they have to name those words.
5. A local pre-push hook (`githooks/pre-push.py`) prints the full list of files in the commits about to be pushed and the repo's visibility (`gh repo view --json visibility`). If the repo is public and anything is not plain source code or docs, the push stops until the owner gives an explicit OK (`PUSH_OK=1 git push ...`).
6. Git hooks are local and are not pushed. Reinstall them on any fresh clone (each Pi, the laptop) with `python3 githooks/install.py`.

## 4. Audits
When asked to audit what has already been pushed: check the working tree and `git status`; search the ENTIRE history (`git log --all --name-only` and the content of every commit) for emails, phone numbers, passwords, tokens, and the file types in section 1; check every branch, tag and the remote. Report findings as a short list (file or string, which commits, whether it is public). Do NOT fix anything during an audit: no deleting, no history rewrite, no force push without approval.

## 5. If something sensitive is found
1. Stop and say plainly what was exposed and since when.
2. Deleting a file in a new commit does not remove it from history. Anything that was public is exposed.
3. Propose a cleanup plan: the right .gitignore, remove the files from tracking, rewrite history with git filter-repo, force push. Wait for approval before any history rewrite or force push, and back up the repo first.
4. List every credential that appeared so it can be rotated (email app passwords, tokens, keys). Rotating is required even after history is cleaned.
5. Remind the owner to check for forks and cached copies, and that GitHub support can purge cached views if needed.

## 6. Ongoing habits
- Push only to the remote the owner names, and only the branch the owner names.
- Keep commits small, with clear messages that describe code changes only, never job-search details.
- If unsure whether a file is safe, assume it is not, leave it out, and ask.
