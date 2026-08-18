# AD2 — real per-invocation cost CV/skew, measured (not assumed)

**Trigger**: AD1 established that the published power table cannot rest on
any single assumed CV (0.15 from the original generator, 0.6 from AC1's
skew probe) — both were unmeasured. AD1 replaced the single power figure
with a CV-swept table instead of picking a winner. AD2 is the other half:
actually measure a real CV, zero-cost, so the swept table has at least one
real data point to anchor against.

## 2.1/2.2 — Evalset and run

`reports/ad2_evalset.json`: 36 hand-authored cases across three
genuinely-different complexity tiers (12 cases/tier, not near-identical
prompts):

- **A_trivial**: short factual recall ("What is the capital of France?").
- **B_synthesis**: short reasoning/synthesis (1 paragraph or a few
  sentences — e.g. "Explain why the sky is blue").
- **C_complex**: multi-step or longer-form generation (200+ word essays,
  step-by-step derivations, multi-phase plans).

`scripts/measure_real_cv_ollama.py` ran all 36 cases through a real
`google-adk` `LlmAgent` (`LiteLlm(model="ollama_chat/qwen2.5:7b")`, local,
zero-cost, no network call) via the shipped `TraceGaugeUsagePlugin` +
`UsageStore` + `snapshot` pipeline — the same capture mechanism a real
user's CI gate uses, not a bespoke measurement path. Real per-invocation
`prompt_token_count`/`candidates_token_count` came back from Ollama for
every case; 0 skipped, 0 unpriced.

Ollama's actual API cost is $0 (self-hosted). To get a representative-
magnitude `cost_usd` figure to compute CV over, a synthetic price-table
entry was registered (mirrors gemini-2.5-flash-lite: $0.10/$0.40 per Mtok
in/out) via `ADK_TRACEGAUGE_PRICE_TABLE`, overriding only the bundled
`__local_zero_cost__` entry. **The dollar figures below are notional. The
token counts underneath them are real** — this is exactly AD2.2's own
framing ("zero-cost first: local Ollama with a synthetic price table,
which gives real token variance even if the dollars are notional").

## 2.4 — Measured, with domain of validity stated

Full data: `reports/ad2_real_cv_measurement.json`.

| Quantity | mean | sd | **CV** | **skewness** |
|---|---|---|---|---|
| `cost_usd` (synthetic $, real tokens) | $0.000180 | $0.000177 | **0.983** | **0.742** |
| `tokens_input` | 107.8 | 17.7 | 0.164 | 0.631 |
| `tokens_output` | 423.2 | 438.5 | **1.036** | 0.745 |

Cost CV is driven almost entirely by output-token variance (CV=1.036), not
input/prompt-token variance (CV=0.164) — consistent with the evalset design
(all prompts are single-sentence instructions of similar length; task
DEMAND, not prompt length, is what varies across tiers, and that shows up
in how much the model writes back, not how much it's asked). Per-tier cost
values (raw, from the JSON, in evalset order): tier A ranges
$0.0000154–$0.0000512, tier B ranges $0.0000724–$0.000190, tier C ranges
$0.000291–$0.000563 — clean, non-overlapping separation by design tier,
confirming the evalset's complexity spectrum produced real, correspondingly
varying cost, not noise.

**Domain of validity, stated plainly (AD2.4)**: this is one evalset (36
hand-authored cases), one local model (`qwen2.5:7b`, 7B-parameter class),
each case run exactly once. **Not a general claim about real per-invocation
ADK cost variance** — a different evalset or a different model could show a
materially different CV/skew. Also **not a measurement of paired mode's
governing quantity**: paired mode's power depends on *within-case* CV (cost
variability for repeated runs of the *same* eval case — see the README's
new "Power depends on your own cost variance" section), and every case here
ran exactly once, so this measurement speaks only to the two-sample-relevant
raw CV, not the paired-relevant within-case CV. Measuring the latter would
require re-running each case several times and is not attempted here.

## 2.5 — Real product finding, not softened

Measured CV (0.98, cost; 1.04, output tokens) lands at the **high end** of
AD1's swept grid (CV=1.0 is the grid's own top value). Reading the
two-sample power table at CV≈1.0: **4.15%–8.55% power across n=30–100** —
the gate would essentially never catch a true 10% regression at any
evalset size in this table, under two-sample fallback, if a real workload's
cost variance resembles this measurement. This is not a hypothetical edge
case constructed to make a point — it came from an evalset explicitly
designed to look like ordinary, realistic variation in task complexity (a
mix of short factual questions, medium reasoning, and longer-form writing),
not an adversarial or unusually noisy one.

**What this means for the gate, stated per AD2.5's own instruction not to
soften it**: for a workload whose task complexity varies this much
invocation-to-invocation, a `min_n=30` two-sample evalset is not a
reliable regression gate at the shipped `confidence=0.98` — n would need to
be far larger (extrapolating the table's own trend, likely several hundred)
to reach 80% power at this CV, or the workload needs pairing (a stable
`eval_case_id`/`session_id`) to be viable at all at realistic evalset
sizes. This does not contradict AD1's per-run "achieved power" reporting —
`check` already tells a user this on every run, for their own data — but it
does mean a user who has NOT read that line, and is relying on the old
single "99.22%" README figure, could be trusting a gate that is, for their
actual workload, closer to a coin flip than a reliable check.

## 2.3 — Representativeness of a local 7B model vs. a real hosted call

**UNVERIFIED, flagged rather than assumed**: whether `qwen2.5:7b`'s output-
length distribution resembles a real hosted model's (e.g. Gemini Flash) is
not established here, and there is a real, specific reason for doubt: a
smaller, less RLHF-tuned-for-conciseness local model plausibly produces
MORE variable, less format-consistent output lengths than a production
hosted model — which would mean the measured CV=0.98–1.04 could
overstate real Gemini-call variance, understate it, or be roughly right,
and this script cannot distinguish between those. Input-token CV (0.164)
is likely closer to representative regardless of model choice (tokenizer
differences aside, prompt length is fixed by the evalset, not the model),
but output-token CV is exactly the number most likely to differ by model.

**Routed to GG, not decided unilaterally, per AD2.3's own instruction.**
Exact steps for a small paid run, if wanted:

1. Point the same agent at a real hosted model instead of Ollama:
   `LlmAgent(model="gemini-2.5-flash-lite", ...)` (or any flash-tier
   model), same 36-case evalset (`reports/ad2_evalset.json`), same
   `TraceGaugeUsagePlugin` capture — no synthetic price table needed,
   `gemini-2.5-flash-lite` is already a real priced entry.
2. Requires a real `GOOGLE_API_KEY`/Gemini API credential — not present in
   this environment, not something to acquire or configure without GG's
   sign-off (rule: spending money or a paid API tier is an escalation
   item, not an autonomous decision).
3. **Cost estimate for sizing the ask**: using this run's own measured
   token counts as a rough proxy (real hosted-model output length will
   likely differ, per the representativeness caveat above, but this
   bounds the order of magnitude) — mean 107.8 input + 423.2 output tokens
   x 36 cases, at gemini-2.5-flash-lite's real published rate ($0.10/$0.40
   per Mtok): **≈$0.0065 total, well under a cent per case, a few cents
   even with a 3-5x safety margin for longer real-model outputs.**
4. If run, the exact same `scripts/measure_real_cv_ollama.py` pipeline
   applies with the model string and price-table override swapped out —
   no new capture mechanism needed.

Not run in this session. No paid API call has been made anywhere in this
investigation.
