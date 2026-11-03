"""scripts/refresh_price_fetched_on.py -- weekly refresh of the price table's ``fetched_on`` dates.

Why: the release gate (``check_price_freshness.py --max-age-days 30``) and the test suite fail once
any entry's ``fetched_on`` is more than 30 days old, and the only way to move that date was a hand
edit. On 2026-11-03 every entry had been fetched on 2026-10-30, so the gate (and every PR's CI)
would have gone red on 2026-11-30 with no price having changed. This script lets the weekly
``Price table freshness`` workflow do the routine part of the re-verify.

What it does, and only this:
1. Runs the full vendor audit (``check_price_table_vs_vendor.collect``: every vendor pricing and
   deprecation page, fetched live).
2. For every entry whose audit row is VERIFIED -- and whose deprecation row, if it has one, is not
   failing -- sets ``fetched_on`` to today. Nothing else in the entry changes.
3. Only when the WHOLE audit is clean (no fetch error, no MISMATCH, no UNVERIFIED), also updates
   ``vendor_pages.pages`` (SHA-256 prefix of each page as the audit read it) and
   ``vendor_pages.fetched_at_utc``. On a partial audit those stay as they were, because they
   describe pages the whole table was verified against.

Never changes a rate. An entry whose rate differs from the vendor (MISMATCH) or that could not be
checked (UNVERIFIED) keeps its old ``fetched_on`` and is listed in the PR body for a human. Not
refreshed either, by design: SKIPPED entries (synthetic, ``retired: true``, retired and gone from
the page) and any entry whose ``retired_on`` has arrived (the provenance rule in
tests/test_price_table_provenance.py ties a late ``fetched_on`` to ``vendor_page_last_listed``,
which this script does not touch; such entries are exempt from the freshness gate anyway).

The file is edited line by line, not re-serialised (``json.dumps`` would rewrite ``10.00`` as
``10.0`` and every other formatting choice), and the result is checked: parsing the new text must
give exactly the old table with only the intended fields changed, or the script refuses to write.

Usage:
    python scripts/refresh_price_fetched_on.py                  # dry run: report only
    python scripts/refresh_price_fetched_on.py --write --body-file pr_body.md
In GitHub Actions it also writes ``changed``/``refreshed``/``held`` to ``$GITHUB_OUTPUT``.
Exit 0 whether or not anything was refreshed; exit 2 on an internal inconsistency (no write).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.check_price_table_vs_vendor import (  # noqa: E402
    ANTHROPIC_DEPRECATIONS_URL,
    ANTHROPIC_MD_URL,
    GOOGLE_DEPRECATIONS_URL,
    GOOGLE_HTML_URL,
    OPENAI_DEPRECATIONS_URL,
    OPENAI_MD_URL,
    AuditRow,
    collect,
)

from adk_tracegauge._pricing import is_retired  # noqa: E402

PRICES_PATH = _ROOT / "src" / "adk_tracegauge" / "data" / "gemini_prices.json"
FAILING = frozenset({"MISMATCH", "UNVERIFIED"})
HASH_PREFIX_LEN = 16  # the length the table already records
AUDIT_ATTEMPTS = 3  # whole-audit retries while a vendor page fetch fails

#: The URL each ``vendor_pages.pages`` key is fetched from by the audit. Google's
#: ``ai.google.dev/gemini-api/docs/*`` redirects to ``aistudio.google.com/docs/*`` since 2026-11
#: (see check_price_table_vs_vendor's docstring); the key keeps the URL the entries cite, and the
#: hash is of the page the audit actually read. A key missing here is a refusal, not a guess.
PAGE_FETCH_URL: dict[str, str] = {
    "https://ai.google.dev/gemini-api/docs/pricing": GOOGLE_HTML_URL,
    "https://developers.openai.com/api/docs/pricing.md": OPENAI_MD_URL,
    "https://platform.claude.com/docs/en/about-claude/pricing.md": ANTHROPIC_MD_URL,
    "https://developers.openai.com/api/docs/deprecations.md": OPENAI_DEPRECATIONS_URL,
    "https://platform.claude.com/docs/en/about-claude/model-deprecations.md": (
        ANTHROPIC_DEPRECATIONS_URL
    ),
    "https://ai.google.dev/gemini-api/docs/deprecations": GOOGLE_DEPRECATIONS_URL,
}


class RefreshError(Exception):
    """The edit could not be made exactly as planned; nothing is written."""


@dataclass
class Plan:
    today: date
    refreshed: dict[str, str] = field(default_factory=dict)  # key -> previous fetched_on
    held: list[AuditRow] = field(default_factory=list)  # failing rows, for a human
    errors: list[str] = field(default_factory=list)  # fetch/parse errors
    not_refreshed: dict[str, str] = field(default_factory=dict)  # key -> reason (non-failing)
    page_hashes: dict[str, str] | None = None  # new vendor_pages.pages, or None = unchanged
    page_hash_note: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.refreshed) or self.page_hashes is not None


def page_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:HASH_PREFIX_LEN]


def make_plan(
    prices: dict[str, object],
    rows: list[AuditRow],
    errors: list[str],
    bodies: dict[str, str],
    today: date,
) -> Plan:
    models: dict[str, dict[str, object]] = prices["models"]  # type: ignore[assignment]
    plan = Plan(today=today, errors=list(errors))
    by_key = {r.key: r for r in rows}
    plan.held = [r for r in rows if r.status in FAILING]
    for key, entry in models.items():
        row = by_key.get(key)
        dep = by_key.get(f"{key} (deprecation)")
        if row is None:
            plan.not_refreshed[key] = "no audit row (fails closed)"
        elif row.status != "VERIFIED":
            plan.not_refreshed[key] = row.status
        elif dep is not None and dep.status in FAILING:
            plan.not_refreshed[key] = f"deprecation row {dep.status}"
        elif key.startswith("__") or is_retired(entry, today):
            plan.not_refreshed[key] = "synthetic or retired (exempt from the freshness gate)"
        elif entry.get("fetched_on") == today.isoformat():
            plan.not_refreshed[key] = "already fetched today"
        else:
            plan.refreshed[key] = str(entry.get("fetched_on"))

    if errors or plan.held:
        plan.page_hash_note = "audit not clean, so the page hashes are left as they were"
        return plan
    pages: dict[str, str] = prices["vendor_pages"]["pages"]  # type: ignore[index]
    new: dict[str, str] = {}
    for key in pages:
        url = PAGE_FETCH_URL.get(key)
        if url is None or url not in bodies:
            plan.page_hash_note = (
                f"no fetched body for vendor page {key!r}; hashes left as they were"
            )
            return plan
        new[key] = page_hash(bodies[url])
    if new != pages:
        plan.page_hashes = new
        plan.page_hash_note = "updated (whole audit clean)"
    else:
        plan.page_hash_note = "unchanged (every page hashes as before)"
    return plan


_ENTRY_HEADER = re.compile(r'^    "(?P<key>[^"]+)": \{$', re.M)


def _section(text: str, name: str) -> tuple[int, int]:
    """(start, end) offsets of top-level object ``name`` (two-space indent)."""
    m = re.search(rf'^  "{re.escape(name)}": \{{$', text, re.M)
    if m is None:
        raise RefreshError(f"top-level {name!r} object not found")
    end = re.search(r"^  \}", text[m.end() :], re.M)
    if end is None:
        raise RefreshError(f"end of {name!r} not found")
    return m.end(), m.end() + end.start()


def _replace_once(text: str, start: int, end: int, old: str, new: str, what: str) -> str:
    span = text[start:end]
    if span.count(old) != 1:
        raise RefreshError(f"{what}: expected exactly one {old!r}, found {span.count(old)}")
    return text[:start] + span.replace(old, new) + text[end:]


def apply_plan(text: str, plan: Plan, fetched_at_utc: str) -> str:
    """Return the edited file text, or raise RefreshError. Verified by re-parsing."""
    original = json.loads(text)
    expected = copy.deepcopy(original)
    new_date = plan.today.isoformat()

    m_start, m_end = _section(text, "models")
    headers = list(_ENTRY_HEADER.finditer(text, m_start, m_end))
    spans = {
        h.group("key"): (h.end(), headers[i + 1].start() if i + 1 < len(headers) else m_end)
        for i, h in enumerate(headers)
    }
    # Edit from the end so earlier offsets stay valid.
    for key in sorted(plan.refreshed, key=lambda k: spans.get(k, (0, 0))[0], reverse=True):
        if key not in spans:
            raise RefreshError(f"entry {key!r} not found in the models object")
        start, end = spans[key]
        old = plan.refreshed[key]
        text = _replace_once(
            text, start, end, f'"fetched_on": "{old}"', f'"fetched_on": "{new_date}"', key
        )
        expected["models"][key]["fetched_on"] = new_date

    if plan.page_hashes is not None:
        v_start, v_end = _section(text, "vendor_pages")
        old_at = original["vendor_pages"]["fetched_at_utc"]
        text = _replace_once(
            text,
            v_start,
            v_end,
            f'"fetched_at_utc": "{old_at}"',
            f'"fetched_at_utc": "{fetched_at_utc}"',
            "vendor_pages.fetched_at_utc",
        )
        expected["vendor_pages"]["fetched_at_utc"] = fetched_at_utc
        for page, new_hash in plan.page_hashes.items():
            old_hash = original["vendor_pages"]["pages"][page]
            if old_hash == new_hash:
                continue
            v_start, v_end = _section(text, "vendor_pages")
            text = _replace_once(
                text, v_start, v_end, f'"{page}": "{old_hash}"', f'"{page}": "{new_hash}"', page
            )
            expected["vendor_pages"]["pages"][page] = new_hash

    if json.loads(text) != expected:
        raise RefreshError("re-parsed table differs from the intended edit; refusing to write")
    return text


def render_body(plan: Plan, prices: dict[str, object]) -> str:
    models: dict[str, dict[str, object]] = prices["models"]  # type: ignore[assignment]
    lines = [
        "## What & why",
        "",
        f"Weekly `fetched_on` refresh ({plan.today.isoformat()}), opened by "
        "`.github/workflows/price-freshness.yml` (`scripts/refresh_price_fetched_on.py`). The full "
        "vendor audit ran live against every pricing and deprecation page; entries whose every "
        "claimed rate re-verified get today's `fetched_on`. **No rate is changed by this PR.**",
        "",
        "## Changes",
        "",
        f"- `fetched_on` -> `{plan.today.isoformat()}` for {len(plan.refreshed)} VERIFIED entries:",
    ]
    lines += [f"  - `{k}` (was {v})" for k, v in sorted(plan.refreshed.items())]
    lines.append(f"- `vendor_pages`: {plan.page_hash_note}.")
    if plan.page_hashes is not None:
        old = prices["vendor_pages"]["pages"]  # type: ignore[index]
        lines += [
            f"  - `{p}`: `{old[p]}` -> `{h}`" for p, h in plan.page_hashes.items() if old[p] != h
        ]
    lines += ["", "## NOT updated (needs a human)", ""]
    if not plan.held and not plan.errors:
        lines.append(
            "Nothing: the whole audit is clean (no MISMATCH, no UNVERIFIED, no fetch error)."
        )
    for msg in plan.errors:
        lines.append(f"- COULD NOT FETCH/PARSE: {msg}")
    for r in plan.held:
        lines.append(f"- **{r.status}** `{r.key}`: " + "; ".join(r.detail))
        if r.status == "MISMATCH":
            lines.append(f"  - ours: {r.ours}")
            lines.append(f"  - vendor: {r.vendor}")
    if plan.held or plan.errors:
        lines += [
            "",
            "A changed rate (MISMATCH) is NOT auto-updated: re-read the vendor page, fix the entry "
            "by hand (rate, `fetched_on`, CHANGELOG) in a separate PR. An UNVERIFIED row caused "
            "by a page that could not be fetched keeps its old date and is retried next week (or "
            "by a `workflow_dispatch`). Until every row passes, the vendor check and release.yml "
            "`preflight-prices` stay red.",
        ]
    dated = [
        date.fromisoformat(str(e["fetched_on"]))
        for k, e in models.items()
        if not k.startswith("__") and not is_retired(e, plan.today) and k not in plan.refreshed
    ]
    oldest = min([plan.today, *dated])
    lines += [
        "",
        "## Testing",
        "",
        "The audit itself is the test: every refreshed entry's row was VERIFIED on this run. "
        "The edited file re-parses to the old table with only the listed fields changed "
        "(checked by the script before writing).",
        f"After merge the oldest gated `fetched_on` is {oldest.isoformat()}, so "
        "`check_price_freshness.py --max-age-days 30` passes through "
        f"{date.fromordinal(oldest.toordinal() + 30).isoformat()}.",
        "",
        "## Screenshots",
        "",
        "n/a (data file only; no UI)",
        "",
        "## Risk & rollback",
        "",
        "Only dates (and page hashes) change; pricing is unaffected. Revert = revert this commit. "
        "CI does not start on a PR opened by the Actions bot: close and reopen it as the owner.",
        "",
    ]
    return "\n".join(lines)


def _summary(plan: Plan) -> str:
    return (
        f"refreshed {len(plan.refreshed)} entries, held {len(plan.held)} failing rows, "
        f"{len(plan.errors)} fetch errors; page hashes: {plan.page_hash_note}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="edit the price table in place")
    parser.add_argument("--body-file", type=Path, help="write the PR body here")
    args = parser.parse_args(argv)

    text = PRICES_PATH.read_text(encoding="utf-8")
    prices = json.loads(text)
    # A vendor page fetch fails transiently now and then (one 20 s timeout on the Anthropic page in
    # the 2026-11-03 dry run); re-run the whole audit rather than refresh fewer entries for it.
    for attempt in range(1, AUDIT_ATTEMPTS + 1):
        rows, errors, bodies = collect()
        if not errors:
            break
        print(f"audit attempt {attempt}/{AUDIT_ATTEMPTS}: {len(errors)} fetch error(s): {errors}")
    now = datetime.now(UTC)
    plan = make_plan(prices, rows, errors, bodies, now.date())
    print(_summary(plan))
    for key, reason in sorted(plan.not_refreshed.items()):
        print(f"  not refreshed: {key}: {reason}")
    for r in plan.held:
        print(f"  HELD {r.status}: {r.key}: " + "; ".join(r.detail))

    if plan.changed:
        try:
            # same shape as the table's existing value, e.g. 2026-10-30T13:01:10Z
            new_text = apply_plan(text, plan, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        except RefreshError as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2
        if args.write:
            PRICES_PATH.write_text(new_text, encoding="utf-8", newline="\n")
            print(f"wrote {PRICES_PATH.relative_to(_ROOT)}")
        else:
            print("dry run: nothing written")
    if args.body_file:
        args.body_file.write_text(render_body(plan, prices), encoding="utf-8")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with Path(out).open("a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if plan.changed else 'false'}\n")
            fh.write(f"refreshed={len(plan.refreshed)}\nheld={len(plan.held)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
