# Today page

The daily page of the job search tracker, at `/today`. `server.py` imports
it; it has no server of its own.

| File | What it does |
|---|---|
| `dashboard.py` | Builds the page (`page()`), serves its CSS and JS (`asset()`), and handles its saves (`save()`) |
| `reader.py` | Read-only view of the tracker database (`mode=ro` and `query_only`) |
| `store.py` | The page's own database and config, in `../data/dashboard/` |
| `static/` | The stylesheet and a short script that sends forms as JSON |
| `config.example.json` | Default settings, copied to `data/dashboard/config.json` on the first start |
| `selfcheck.py` | Checks for em dashes and confirms the tracker database refuses writes |

## Settings

Edit `data/dashboard/config.json` and restart the tracker service.

| Key | Default | Meaning |
|---|---|---|
| `done_line` | 2 applications, 1 networking touch, 1 follow-up | The daily target |
| `light_days`, `light_day_line` | Sat, Sun: 1 follow-up | Days with a smaller target |
| `hard_stop` | `17:00` | After this the page says the search is closed, unless a call is today or tomorrow |
| `review_day` | `Fri` | The day the weekly review shows on its own |
| `volume_note_over` | 3 | More applications than this in a day shows one gentle line |
| `stale_days` | 14 | Days without activity before an application is listed as quiet |
| `ghosted_days` | 30 | Days without activity, still Applied, before the page asks about it once |
| `upcoming_days` | 3 | How far ahead Due now looks for calls |
| `resume_versions` | v1, v2, v3 | Choices in the resume dropdown |
| `activities` | a few examples | Suggestions for the non-job activity |

## How it reads the tracker

Activity on an application is its applied date, a status or follow-up date
change, or a log entry linked to it. A response is a recruiter screen or any
later stage, at any point. The tracker has no interview dates, so a call is a
row in Recruiter screen, Interviewing or Awaiting decision whose follow-up
date is close.

Status changes made before the page's first start are never asked about.

Set `DASHBOARD_NOW=YYYY-MM-DDTHH:MM` to render as if it were that time.
