# `pip install adk-tracegauge` -> `adk-tracegauge quickstart` wall-clock, measured 2026-09-20

Replaces the README's previous "78.2s wall-clock" headline, which no re-measurement could
reproduce. Raw logs: `r1.log`..`r4.log`; harness: `measure_install.ps1` (one Stopwatch per step,
a `START`/`END` timestamp line for each). Measured against origin/main
`c28f68fa43bdb25df30cb652afccc5b27b109e1a`; the *published* `adk-tracegauge==0.6.1` wheel was
what got installed (not this checkout).

## Conditions (held identical across all four runs)

- Windows 11 Home, AMD Ryzen 7 6800H, 31.2 GB RAM, Python 3.13.5 (conda base interpreter used
  only to *create* each venv; nothing was installed into it).
- A brand-new venv per run at a short path (`C:\tg\rN`); `pip install --no-cache-dir` so the
  pip wheel/HTTP cache is cold every run. Network: home connection, bandwidth NOT measured.
- Resolved to `google-adk==2.7.1`, `litellm==1.85.7`; 109 installed distributions (108 + this one).
- Nothing else CPU-heavy was run on the machine during the four runs.

## Runs 1-3: the exact README path (one `pip install adk-tracegauge`, then `quickstart`)

| run | venv create | `pip install adk-tracegauge` | `quickstart` | total |
|---|---|---|---|---|
| 1 | 16.7s | 418.2s | 33.7s | **468.7s** |
| 2 | 12.2s | 397.9s | 28.5s | **438.5s** |
| 3 | 10.1s | 331.4s | 30.5s | **372.0s** |

**Median 438.5s (7.3 min); range 372.0s - 468.7s; mean 426.4s.** `quickstart` exits 1 every
time by design (it prints a deliberate regression verdict).

## Run 4: breakdown (deps first, then this package alone)

| step | elapsed | share of 373.5s |
|---|---|---|
| venv create | 9.9s | 2.7% |
| `pip install "google-adk[eval]>=2.6.0,<2.8.0"` (the whole 108-package tree) | 329.4s | 88.2% |
| `pip install adk-tracegauge` (its own wheel; deps already satisfied) | 5.5s | 1.5% |
| `adk-tracegauge quickstart` | 28.7s | 7.7% |

Upstream dependency resolution/download/install is 88% of the total; this package's own
install is 5.5s.

## Could not reproduce 78.2s

No run came within 4x of it. The fastest *pip step alone* (331.4s) is 4.2x the old whole-path
claim. Why 78.2s was ever measured is UNVERIFIED -- untested hypotheses: a warm pip cache and/or
an already-populated global site-packages at the time (this measurement deliberately used neither).
