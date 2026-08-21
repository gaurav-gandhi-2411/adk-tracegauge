"""scripts/check_model_retirement_gemini.py — AN3: free-verifiable model
retirement check, Gemini only.

TWO retirement classes exist for Gemini models, discovered empirically
this session (docs/audit/, AN3) and NOT interchangeable:

1. FULL REMOVAL from the model catalog. Free-verifiable: the model is
   completely absent from the `models.list()` endpoint (a metadata-only
   call -- no generation, no token billing; confirmed empirically this
   session by comparing spend before/after calling it, and by Google's own
   API pricing structure, which only lists per-token costs for
   generate/embed endpoints, not for list/get metadata). This is the ONLY
   class this script checks.
2. ACCOUNT-ELIGIBILITY GATING ("no longer available to new users"). NOT
   free-verifiable: the model stays listed in models.list() with
   generateContent still in its own supported_actions, and the vendor's
   pricing page still lists it -- the ONLY way to discover this class is a
   real generateContent call, which costs money and may succeed or fail
   depending on which account/key is used (an existing key predating the
   cutoff may still work). gemini-2.5-flash-lite is a confirmed live
   example (404 for a new key, 2026-08-21) -- see its
   `new_user_availability_warning` field in gemini_prices.json, which this
   script does NOT try to re-verify (would require a paid call per
   flagged model, defeating the point of a free check). That case is
   handled by manual review + the documented field instead, not
   automation.

Requires GOOGLE_API_KEY (or GEMINI_API_KEY) to run the live check --
SKIPS gracefully (exit 0, not a failure) if neither is set, so this does
not break CI for anyone without a stored key. When a key IS available,
flags any gemini_prices.json entry whose model name does not appear
ANYWHERE in the live models.list() response as a CANDIDATE for
`"retired": true` -- reported for human review, not auto-applied (a name
mismatch due to a renaming this script doesn't know about would otherwise
silently mark a live model retired).

Zero-cost when it runs (models.list() is free, per the class-1 reasoning
above) -- distinct from every other real-money script in this
repo (measure_real_cv_gemini.py etc.), which this script does NOT import
or depend on.

Run: ``uv run python scripts/check_model_retirement_gemini.py``
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from adk_tracegauge._pricing import load_gemini_prices  # noqa: E402


def _announce_skip(message: str) -> None:
    """AP4.4: a step that exits 0 shows as a plain green checkmark in
    GitHub's checks UI -- indistinguishable, without opening the log, from
    a check that actually ran and found nothing wrong. A skipped check
    that reads as coverage is worse than no check at all. Emits a
    `::warning::` workflow command (renders as a yellow annotation on the
    PR's checks summary, visible without opening any log) in addition to
    the stdout message, plus a $GITHUB_STEP_SUMMARY entry (visible on the
    workflow run's own summary page) when running in Actions. No-ops
    outside Actions (e.g. a local `uv run` -- $GITHUB_STEP_SUMMARY unset)."""
    print(message)
    # GitHub Actions workflow command -- newlines must be escaped, this
    # message has none, but guard anyway rather than assume future callers.
    single_line = message.replace("\n", " ")
    print(f"::warning::{single_line}")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(f"### :warning: Model retirement check SKIPPED\n\n{message}\n")


def main() -> int:
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        _announce_skip(
            "SKIPPED: no GOOGLE_API_KEY/GEMINI_API_KEY in environment -- "
            "this check requires a live models.list() call and cannot run "
            "without one. Not a failure (exit 0): this is the expected "
            "no-op for any environment without a stored key -- but it also "
            "means full-removal retirements (like gemini-2.0-flash before "
            "this check existed) are NOT being caught right now. To "
            "activate: add a repo secret named exactly GOOGLE_API_KEY "
            "(Settings > Secrets and variables > Actions > New repository "
            "secret), value = a free Gemini API key from "
            "https://aistudio.google.com/apikey (models.list() is "
            "metadata-only, no generation billing -- the free tier is "
            "sufficient, no paid tier needed for this specific check)."
        )
        return 0

    try:
        from google import genai
    except ImportError:
        _announce_skip(
            "SKIPPED: google-genai not importable in this environment -- "
            "this should not happen in adk-tracegauge's own CI (it's a "
            "core dependency), only in an unusual manual invocation. Not a "
            "failure (exit 0), but also not a real check having run."
        )
        return 0

    client = genai.Client(api_key=api_key)
    try:
        live_names = {m.name.removeprefix("models/") for m in client.models.list()}
    except Exception as e:  # noqa: BLE001 -- report and fail closed, don't guess
        print(
            f"FETCH FAILURE: could not list live Gemini models: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        print(
            "This means 'we don't know if our retirement flags are "
            "current', not 'they are current' -- failing the run rather "
            "than silently passing.",
            file=sys.stderr,
        )
        return 1

    prices = load_gemini_prices()
    models: dict[str, dict[str, object]] = prices["models"]

    # Synthetic tier-only entries (e.g. "gemini-2.5-pro-long-context") are
    # never real API model names -- they're applied internally by
    # _pricing.resolve_model_for_call based on token count, never matched
    # against a raw ADK model_version string, and so never appear in
    # models.list() by construction. Excluded via the SAME structural
    # signal _pricing.py itself uses to identify them: being the target of
    # another entry's long_context_model_key. (Caught in this script's own
    # first test run: both currently-defined long-context entries showed up
    # as false-positive "candidates" before this exclusion was added.)
    synthetic_tier_targets = {
        entry["long_context_model_key"]
        for entry in models.values()
        if "long_context_model_key" in entry
    }

    gemini_entries = {
        key: entry
        for key, entry in models.items()
        if key.startswith("gemini-")
        and not entry.get("retired")
        and key not in synthetic_tier_targets
    }

    candidates = [key for key in gemini_entries if key not in live_names]

    if not candidates:
        print(
            f"OK: all {len(gemini_entries)} non-retired Gemini entries in "
            f"gemini_prices.json are present in the live models.list() "
            f"catalog ({len(live_names)} models listed)."
        )
        return 0

    print(
        "CANDIDATE FULL-REMOVAL RETIREMENTS (absent from live models.list(), "
        "not currently marked 'retired'):",
        file=sys.stderr,
    )
    for key in sorted(candidates):
        print(f"  - {key}", file=sys.stderr)
    print(
        "\nThis is a candidate list for human review, not an automatic "
        "verdict -- a name mismatch from an undocumented rename would "
        "produce a false positive here. Confirm each candidate is genuinely "
        'gone (not just renamed) before adding "retired": true and a '
        "retired_on date to src/adk_tracegauge/data/gemini_prices.json. "
        "This check does NOT catch account-eligibility gating (models that "
        "stay listed but 404 for some keys) -- see this script's own "
        "module docstring for why that class isn't free-verifiable.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
