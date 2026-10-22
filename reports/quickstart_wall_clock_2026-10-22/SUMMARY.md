# Quickstart wall clock, published 0.10.1 (2026-10-22)

Following only the README's "Try it right now" section, from an empty virtualenv:

```bash
pip install adk-tracegauge   # pinned here as adk-tracegauge==0.10.1, --no-cache-dir
adk-tracegauge quickstart
```

| Step | Seconds |
|---|---|
| `pip install --no-cache-dir adk-tracegauge==0.10.1` (cold pip cache) | 83.2 |
| `adk-tracegauge quickstart` (to the printed REGRESSION verdict) | 6.4 |
| Total, empty venv to verdict | 89.5 |

Single run. Windows 11, Python 3.13 (`uv venv --seed`), home network. Resolved `google-adk==2.12.0`;
50 distributions in the venv after install (including pip). Harness: `measure.sh`; output:
`quickstart.log`, `version.log`. Run from a short directory: the same install from a long temp path failed on
Windows MAX_PATH (a google-adk file under `integrations/bigquery/skills/...`), which is the user's environment,
not this package.

`adk-tracegauge quickstart` exited 1. That is by design (`_quickstart.py`: the demo injects a regression, so the
gate always returns 1). The first output line is the soft "newer than tested" note for google-adk 2.12.0, expected
on 0.10.1 (tested below 2.12.0).
