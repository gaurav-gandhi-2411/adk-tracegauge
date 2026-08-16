"""adk_tracegauge/_quickstart.py -- HH1.1: `adk-tracegauge quickstart`, a
one-command, zero-config demo that fires a real regression gate immediately
after `pip install`, with no ADK app of the user's own, no API key, and no
live model call.

Reuses the exact mechanism `examples/05_hand_rolled_session_id_pairing.py`
already proves live (a deterministic fake `BaseLlm`, driven through a real
`InMemoryRunner`, with `TraceGaugeUsagePlugin` wired via the documented
`after_model_callback` pattern) -- not a new, unverified code path. The
only difference from that example: this runs as an installed console
subcommand (`adk-tracegauge quickstart`) instead of a script the user has
to clone the repo to find, and everything it needs (the toy agent, the toy
"eval cases") is defined in this module, not read from disk -- so `pip
install adk-tracegauge && adk-tracegauge quickstart` is the entire
onboarding path, no files to create, no repo to clone.

Deterministic and fast by construction: `N_CASES=32` (one case over
`--min-n=30`, so the real paired-mode gate genuinely fires, not a
demo that bypasses the refusal floor), a fixed seed for the bootstrap CI
(`_regression.py`'s own `DEFAULT_SEED=42`, unchanged), and a fake LLM whose
token counts are a pure function of the case index -- the printed numbers
are exactly reproducible run to run, on any machine, with no network.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from adk_tracegauge._plugin import TraceGaugeUsagePlugin
from adk_tracegauge._store import UsageStore
from adk_tracegauge.snapshot import write_snapshot

N_CASES = 32
CASE_LEVEL_BASE_TOKENS = 5_000
CASE_LEVEL_STRIDE = 4_723
CASE_LEVEL_MOD = 25_000
REGRESSION_BUMP_PROMPT_TOKENS = 6_000

_CASE_RE = re.compile(r"case (\d+)")


def _case_level_prompt_tokens(case_idx: int) -> int:
    return CASE_LEVEL_BASE_TOKENS + (case_idx * CASE_LEVEL_STRIDE) % CASE_LEVEL_MOD


class _QuickstartFakeLlm(BaseLlm):
    """No network call, no API key, no real cost -- token usage is a pure,
    deterministic function of the case index, plus a fixed regression bump
    on the "current" variant only. Identical mechanism to
    examples/05's `_CaseDependentFakeLlm`."""

    model: str = "quickstart-fake-model"
    regression_bump: int = 0

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["quickstart-fake-model"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        text = "".join(p.text or "" for c in (llm_request.contents or []) for p in (c.parts or []))
        match = _CASE_RE.search(text)
        case_idx = int(match.group(1)) if match else 0
        prompt_tokens = _case_level_prompt_tokens(case_idx) + self.regression_bump
        yield LlmResponse(
            model_version="gemini-2.5-flash",
            content=genai_types.Content(parts=[genai_types.Part(text="4")], role="model"),
            usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                prompt_token_count=prompt_tokens,
                candidates_token_count=50,
                cached_content_token_count=0,
                total_token_count=prompt_tokens + 50,
            ),
        )


async def _run_variant(app_name: str, regression_bump: int, store: UsageStore) -> None:
    plugin = TraceGaugeUsagePlugin(store=store)
    agent = LlmAgent(
        name=app_name,
        model=_QuickstartFakeLlm(regression_bump=regression_bump),
        instruction="Answer the question.",
        # google-adk's own LlmAgent.after_model_callback type stub wants a
        # plain positional Callable[[Context, LlmResponse], ...]; this
        # plugin method declares callback_context as keyword-only (see
        # _plugin.py's own after_model_callback docstring for why -- it's
        # the one hook proven to fire during `adk eval`/AgentEvaluator, the
        # documented wiring this repo's own README/"Use with agent" section
        # recommends). Python itself has no issue calling it this way at
        # runtime (verified live: this exact wiring is what
        # examples/05_hand_rolled_session_id_pairing.py already runs) --
        # this is a real, pre-existing static-typing mismatch between the
        # plugin's declared signature and ADK's Callable protocol, not a
        # runtime bug.
        after_model_callback=plugin.after_model_callback,  # type: ignore[arg-type]
    )
    runner = InMemoryRunner(agent=agent, app_name=app_name, plugins=[plugin])
    user_id = "u"
    for i in range(N_CASES):
        session_id = f"case-{i}"  # pinned identically across baseline/current
        await runner.session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )
        new_message = genai_types.Content(
            parts=[genai_types.Part(text=f"case {i}: what is 2+2?")], role="user"
        )
        async for _event in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=new_message
        ):
            pass


def run_quickstart() -> int:
    """Runs the full demo end to end and returns `check`'s real exit code
    (deliberately regressed, so this always returns 1 -- matching every
    other example in this repo's own convention of never dressing up a
    regression as a clean pass)."""
    from adk_tracegauge._cli import main as tracegauge_main

    print(
        "adk-tracegauge quickstart -- no API key, no network call, no ADK app of your own.\n"
        "Running a deterministic, in-memory demo agent through a real InMemoryRunner,\n"
        f"twice ({N_CASES} toy cases each), with a deliberate cost regression injected\n"
        "into the second run, then firing the real `adk-tracegauge check` gate.\n"
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        baseline_store = UsageStore()
        current_store = UsageStore()

        asyncio.run(_run_variant("baseline_app", 0, baseline_store))
        asyncio.run(_run_variant("current_app", REGRESSION_BUMP_PROMPT_TOKENS, current_store))

        baseline_path = tmp_path / "baseline.json"
        current_path = tmp_path / "current.json"
        write_snapshot(baseline_store, baseline_path)
        write_snapshot(current_store, current_path)

        exit_code = tracegauge_main(
            ["check", "--baseline", str(baseline_path), "--current", str(current_path)]
        )
        print(
            "\nThis ran entirely from what shipped in the installed package -- no files "
            "were read from your machine. Next: wire TraceGaugeUsagePlugin into your own "
            "agent (see README, 'Use with agent') and run this same `check` command "
            "against your own two snapshots."
        )
        return exit_code


__all__ = ["run_quickstart"]
