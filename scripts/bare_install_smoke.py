"""scripts/bare_install_smoke.py -- run with the interpreter of a venv that has ONLY bare ``google-adk``
(no ``[eval]`` extra, so no pandas) plus this package's wheel. Exercises everything the base install
promises: the import, the plugin on a real ``InMemoryRunner`` run, ``CostEfficiencyEvaluator``, and
every CLI subcommand (``snapshot``, ``check``, ``report`` in all three forms, ``quickstart``) with the
exact exit code each is documented to return. Exits non-zero on the first surprise.

Used by the ``bare-adk`` CI job on the google-adk floor (2.6.0) and the newest verified release
(2.9.2): the ``lint-and-test`` job installs the ``[eval]`` extra and so can never see a base-install
break (a new top-level import of pandas & co.), by construction.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ENTRY = textwrap.dedent(
    """
    from __future__ import annotations
    import random
    from adk_tracegauge._store import DEFAULT_USAGE_STORE, CapturedCall

    def _populate(seed: int, mean_out: float) -> None:
        rng = random.Random(seed)
        for i in range(40):
            out = max(1000, int(rng.gauss(mean_out, mean_out * 0.1)))
            DEFAULT_USAGE_STORE.record(
                f"inv-{seed}-{i}",
                CapturedCall(
                    model_version="gemini-2.5-flash-lite", prompt_token_count=5_000,
                    candidates_token_count=out, cached_content_token_count=0,
                    total_token_count=5_000 + out, agent_name="root_agent",
                ),
            )

    def run_baseline() -> None:
        DEFAULT_USAGE_STORE.clear()
        _populate(1234, 20_000)

    def run_current() -> None:
        DEFAULT_USAGE_STORE.clear()
        _populate(42, 24_000)
    """
)


def fail(msg: str) -> None:
    print(f"BARE-INSTALL SMOKE FAILED: {msg}", file=sys.stderr)
    raise SystemExit(1)


def cli(*args: str, expect: int, cwd: Path) -> str:
    exe = Path(sys.executable).parent / (
        "adk-tracegauge.exe" if sys.platform == "win32" else "adk-tracegauge"
    )
    r = subprocess.run(  # noqa: S603 -- fixed argv
        [str(exe), *args], capture_output=True, text=True, cwd=cwd, timeout=600, check=False
    )
    if r.returncode != expect:
        fail(
            f"`adk-tracegauge {' '.join(args)}` exited {r.returncode}, expected {expect}\n{r.stdout}\n{r.stderr}"
        )
    print(f"ok  adk-tracegauge {' '.join(args)}  -> exit {r.returncode}")
    return r.stdout


async def plugin_run() -> int:
    from google.adk.agents.llm_agent import LlmAgent
    from google.adk.apps.app import App
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types as T

    from adk_tracegauge import TraceGaugeUsagePlugin
    from adk_tracegauge._store import UsageStore

    class Fake(BaseLlm):
        model: str = "fake"

        @classmethod
        def supported_models(cls) -> list[str]:
            return ["fake"]

        async def generate_content_async(self, llm_request, stream: bool = False):  # type: ignore[no-untyped-def]
            yield LlmResponse(
                model_version="gemini-2.5-flash",
                content=T.Content(role="model", parts=[T.Part(text="hi")]),
                usage_metadata=T.GenerateContentResponseUsageMetadata(
                    prompt_token_count=1000, candidates_token_count=200, total_token_count=1200
                ),
            )

    store = UsageStore()
    app = App(
        name="smoke",
        root_agent=LlmAgent(name="a", model=Fake(), instruction="x"),
        plugins=[TraceGaugeUsagePlugin(store=store)],
    )
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(app_name="smoke", user_id="u")
    async for _ in runner.run_async(
        user_id="u",
        session_id=session.id,
        new_message=T.Content(role="user", parts=[T.Part(text="q")]),
    ):
        pass
    return sum(len(store.get(i)) for i in store.invocation_ids())


def main() -> None:
    import importlib.metadata as md

    if importlib.util.find_spec("pandas") is not None:
        fail("pandas is importable: this venv is not a bare google-adk install")
    print(f"google-adk {md.version('google-adk')}, python {sys.version.split()[0]}, pandas absent")

    import adk_tracegauge

    if adk_tracegauge.EVAL_REGISTRATION_SKIPPED_REASON is None:
        fail("expected the metric registration to be skipped on a bare install")
    print(
        f"ok  import adk_tracegauge {adk_tracegauge.__version__} (registration skipped: "
        f"{adk_tracegauge.EVAL_REGISTRATION_SKIPPED_REASON})"
    )

    calls = asyncio.run(plugin_run())
    if calls != 1:
        fail(f"plugin captured {calls} model calls in a real InMemoryRunner run, expected 1")
    print("ok  TraceGaugeUsagePlugin captured 1 call in a real InMemoryRunner run")

    from google.adk.evaluation.eval_case import Invocation
    from google.adk.evaluation.eval_metrics import EvalMetric
    from google.genai import types as T

    from adk_tracegauge._store import CapturedCall, UsageStore
    from adk_tracegauge.evaluator import (
        METRIC_NAME,
        CostEfficiencyEvaluator,
        CostThresholdCriterion,
    )

    store = UsageStore()
    store.record(
        "e-1",
        CapturedCall(
            model_version="gemini-2.5-flash",
            prompt_token_count=1000,
            candidates_token_count=200,
            cached_content_token_count=0,
            total_token_count=1200,
        ),
    )
    ev = CostEfficiencyEvaluator(
        eval_metric=EvalMetric(
            metric_name=METRIC_NAME, criterion=CostThresholdCriterion(threshold=1.0)
        ),
        store=store,
    )
    res = ev.evaluate_invocations(
        [Invocation(invocation_id="e-1", user_content=T.Content(parts=[]))]
    )
    status = str(res.per_invocation_results[0].eval_status)
    if "PASSED" not in status:
        fail(f"CostEfficiencyEvaluator status {status}, expected PASSED")
    print(f"ok  CostEfficiencyEvaluator -> {status}")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "smoke_entry.py").write_text(ENTRY, encoding="utf-8")
        cli(
            "snapshot",
            "--entrypoint",
            "smoke_entry:run_baseline",
            "--output",
            "baseline.json",
            expect=0,
            cwd=work,
        )
        cli(
            "snapshot",
            "--entrypoint",
            "smoke_entry:run_current",
            "--output",
            "current.json",
            expect=0,
            cwd=work,
        )
        cli("check", "--baseline", "baseline.json", "--current", "current.json", expect=1, cwd=work)
        out = cli("report", "baseline.json", expect=0, cwd=work)
        if "priced total" not in out and "total:" not in out:
            fail(f"report <snapshot> printed no total:\n{out}")
        data = json.loads(cli("report", "baseline.json", "--json", expect=0, cwd=work))
        if "unverifiable_pricing" not in data:
            fail("report --json has no unverifiable_pricing key")
        cli("report", "--entrypoint", "smoke_entry:run_baseline", expect=0, cwd=work)
        cli("quickstart", expect=1, cwd=work)  # the demo injects a deliberate regression -> exit 1
    print("BARE-INSTALL SMOKE OK")


if __name__ == "__main__":
    main()
