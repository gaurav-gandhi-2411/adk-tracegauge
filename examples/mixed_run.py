"""A three-invocation mixed run: two priced Gemini calls and one call to a model with no price.

This RECONSTRUCTS the `mixed_run:run` fixture behind the `adk-tracegauge report` output in the public reply
on google/adk-python Discussion #97 (2026-09-20). The original file no longer exists; this one was rebuilt
from the token counts printed in that reply (12,000 in / 800 out and 3,500 in / 420 out on
gemini-2.5-flash, then a call to `acme-llm-7b`) and reproduces its table: $0.005600 + $0.002100 =
$0.007700 priced, 15,500 in / 1,220 out, one invocation reported unknown and EXCLUDED rather than guessed.
The third call's token counts (2,000 in / 100 out) were not shown in the reply, because an unknown model
is never priced; the invocation ids differ too (the real run generated random ones, this fixture uses
fixed ids). `tests/test_mixed_run_example.py` asserts the published figures.

Run it from this directory, with no API key and no network:

    cd examples
    adk-tracegauge report --entrypoint mixed_run:run

By hand: 12,000 x $0.30/M + 800 x $2.50/M = $0.0036 + $0.0020 = $0.0056;
3,500 x $0.30/M + 420 x $2.50/M = $0.00105 + $0.00105 = $0.0021.
"""

from __future__ import annotations

from adk_tracegauge._store import DEFAULT_USAGE_STORE, CapturedCall

# (invocation id, model, prompt tokens, output tokens). The last one has no price-table entry.
CALLS = (
    ("e-inv-1", "gemini-2.5-flash", 12_000, 800),
    ("e-inv-2", "gemini-2.5-flash", 3_500, 420),
    ("e-inv-3", "acme-llm-7b", 2_000, 100),
)


def run() -> None:
    """Populate the shared usage store the way a plugin-instrumented eval run would."""
    DEFAULT_USAGE_STORE.clear()
    for invocation_id, model, tokens_in, tokens_out in CALLS:
        DEFAULT_USAGE_STORE.record(
            invocation_id,
            CapturedCall(
                model_version=model,
                prompt_token_count=tokens_in,
                candidates_token_count=tokens_out,
                cached_content_token_count=0,
                total_token_count=tokens_in + tokens_out,
                agent_name="root_agent",
            ),
        )


if __name__ == "__main__":
    run()
    print(
        f"populated {len(CALLS)} invocations; now run: adk-tracegauge report --entrypoint mixed_run:run"
    )
