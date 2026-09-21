# Design: pricing the Google Search grounding fee

Status: DRAFT for the owner, 2026-09-21. Nothing here is implemented. Written from files already on
disk (adk-tracegauge `main` `9da795c`, the cost-correctness suite at `f8ac7f2`, google-adk 2.9.2 and
google-genai as installed in the suite venv). **No vendor page was re-fetched for this document**;
every price below is the figure recorded in the suite's case sources (`ai.google.dev/gemini-api/docs/pricing`
and `cloud.google.com/vertex-ai/generative-ai/pricing`, both "read 2026-09-21"). Anything not in
those recorded sources, or not read from source code, is marked **UNVERIFIED**.

## Findings from real responses (captured 2026-09-21) -- read this first

Captured through ADK 2.9.2's built-in `google_search` and `GoogleSearchAgentTool` on the Gemini API with a
free-tier key (raw data, verbatim `model_dump` of every model call: `docs/design/data/real_grounding_2026-09-21.jsonl` (one line per model call, plus one per scenario the free-tier 429 stopped; runs 1-3 merged); 7 grounded `gemini-2.5-flash` requests (6 seen by adk-tracegauge), one
ungrounded call, no successful Gemini 3.x grounded call). Each item is marked VERIFIED (observed) or still
UNVERIFIED.

**One finding breaks the design as first written, and it is stated plainly here:** every grounded Gemini
2.5 response (6 of 6 that adk-tracegauge saw) carried `tool_use_prompt_token_count` (115-148 tokens, with a
matching `tool_use_prompt_tokens_details`), and `total_token_count = prompt + candidates + thoughts +
tool_use` (44 + 39 + 432 + 119 = 634, VERIFIED arithmetic). `_adapter` has always refused to price a call
with nonzero tool-use tokens, so **a real Google Search invocation is reported `unknown` today and never
reaches #86's grounding flag**; the flag applies only when tool-use tokens are 0, which is how the suite's
G1-G3 fixtures were built (they set none), so G1-G3 did not represent real responses. Consequence for the
plan: the fee cannot be priced, or even reached by #86/#89's flag, while the tool-use tokens make the whole
invocation `unknown`.

**Decision (owner, 2026-09-21), superseding this document's earlier recommendation (a):** an earlier draft
recommended pricing the tool-use tokens as input with a stated assumption (about 120-150 tokens is ~$0.00004
at $0.30/M, three orders of magnitude below the $0.035 fee). That conflicted with the Vertex page's statement
that Google Search grounding input tokens are not charged, and the Gemini API treatment is UNVERIFIED (the
URL-context doc says its tool tokens count as input, so vendor treatment varies by tool). Therefore:

1. **Tool-use tokens stay UNPRICED until the fee model exists.** They become an unpriced component
   (`tool_use_prompt_tokens`) with a message naming them and saying why, exactly like audio input: the rest of
   the invocation prices normally, the row is marked `*`, the total is INCOMPLETE, and #89's source-named
   grounding flag now appears on real grounded calls. This ships as adk-tracegauge 0.9.1 (with #89).
2. **Once the fee model exists,** the grounded total is an **upper bound** (paid-rate grounding fee, free
   allowance not observable; section 2's `total_is_upper_bound`). Folding the tool-use tokens in at the input
   rate is then consistent with that upper bound, because an upper bound may include a charge Google might
   waive. That is a decision for the fee-model PR, not for 0.9.1; until then they are left out and flagged.
3. **Gemini 3.x captures are deferred.** They need a paid tier (a free-tier key gets 429 on every grounded
   3.x request). Billing is NOT to be enabled for this; every 3.x item in the tables below stays UNVERIFIED
   until someone with a paid project supplies a capture.

New suite cases with realistic `tool_use_prompt_token_count` are needed (section 6); they are the G1r-G3r
twins added to the cost-correctness suite.

| Question | Result |
|---|---|
| Does the response carry `grounding_metadata` when Search ran? | VERIFIED: A, C (x2), D, F, G: `web_search_queries` + `search_entry_point` always; `grounding_chunks` + `grounding_supports` in 3 of 6 |
| How many queries per call? | VERIFIED: 1-2 in one call (A: 2, G: 2, F: 2, D: 1, C: 1 per sub-agent call). So per-query and per-prompt pricing differ by 2x on ordinary prompts |
| Does `len(web_search_queries)` equal what a per-request fee bills? | **Still UNVERIFIED.** The key is on the free tier (429 text named `generate_content_free_tier_requests`, limit 5/min for gemini-2.5-flash), so nothing was invoiced and no billing figure could be compared |
| Zero-result prompt: metadata present? | VERIFIED (n=1): the response still carried `web_search_queries` (the exact nonsense string) and `search_entry_point`, `grounding_chunks` absent, 130 tool-use tokens, empty answer. Whether it is billed: **UNVERIFIED** |
| Is `grounding_chunks` a reliable "sources returned" signal? | **No.** VERIFIED: A and F produced a correct answer with no `grounding_chunks` at all. Vertex's "billed only when sources return" cannot be decided from chunks; and the Vertex response shape is UNVERIFIED |
| Search tool enabled but the model did not search (E: "17 x 23") | VERIFIED (n=1): no `grounding_metadata`, no tool-use tokens: not a grounded prompt |
| Agent loop: how many grounded prompts? | VERIFIED (n=1): 5 model calls in one user turn (root, sub-agent, root, sub-agent, root); exactly the 2 sub-agent calls carried grounding metadata, each with 1 query and its own invocation. The root calls carried none. So the count is per model call, and the fee attaches to the sub-agent's invocation |
| Streaming | VERIFIED (n=1): the `partial=True` chunk already carried the complete metadata and usage, and the final chunk repeated both identically: counting once per call group is right, the OR-over-chunks in #86 is harmless but not required for 2.5 |
| Backend detection | VERIFIED on google-adk 2.9.2, Gemini API key: `callback_context._invocation_context.agent.canonical_model.api_client.vertexai` returned `False` for root and sub-agent calls; `usage_metadata.traffic_type` was absent. Behaviour on Vertex, on 2.6-2.8, and on `adk eval`: UNVERIFIED |
| Gemini 3.x grounded response | **UNVERIFIED, none obtained.** `gemini-3.5-flash`, `-3.5-flash-lite`, `-3.6-flash`, `-3-flash-preview` each returned 429 RESOURCE_EXHAUSTED (no metric named) to a grounded request on this key; an ungrounded 3.5-flash-lite call succeeded. The cause is unknown (consistent with 3.x grounding needing a paid tier). Every 3.x item below stays UNVERIFIED until a paid-tier capture |

Spend: **$0.00 charged** (free-tier quota metric on every 2.5 call; the account dashboard was not visible,
so this is UNVERIFIED as an invoice fact). If every grounded 2.5 call had been billed it would be at most
7 x $0.035 + about $0.01 of tokens = about $0.26. Failed 429 requests are not billed.

## Where we are

(Superseded in part by the decision above: from 0.9.1 tool-use tokens no longer make the invocation
unknown.) #86 flags a grounded invocation **that carries no tool-use tokens**: its tokens are priced, the grounding
fee is left out, the row is marked `*`, `--json` says `total_is_complete: false`, and the eval metric returns
`NOT_EVALUATED`. The suite scores G1-G3 INCOMPLETE (flagged) because its fixtures set no tool-use tokens;
real Google Search responses always do (see the findings above), so a real invocation is `unknown` today.
PR #89 (not merged when this was written) makes the flag name its source (Google Search, Vertex AI Search,
Maps, unknown). This design prices the fee where that can be done honestly and leaves the flag in place
where it cannot.

Scope of v1: **Google Search grounding on the Gemini API only.** Google Maps, image search, Vertex AI
Search / retrieval grounding and the Vertex backend keep the #86 flag (sections 3 and 4).

## 1. Pricing model and how each is identified

| | Gemini 2.x (suite: `gemini-2.5-flash`) | Gemini 3.x (suite: `gemini-3.5-flash`) |
|---|---|---|
| Unit | one grounded **prompt** (one model call) | one search **query** |
| Rate | $35 / 1,000 grounded prompts = $0.035 | $14 / 1,000 search requests = $0.014 |
| Charge | one charge however many queries the call ran | one charge per query |
| Suite case | G1: 3 queries -> $0.035 | G2: 3 queries -> 3 x $0.014 = $0.042; G3: 1 query -> $0.014 |

Sources: suite `cases.py` header and G1-G3 `sources=(GEMINI_PAGE, VERTEX_PAGE)`; the 3.x figure is
stated to be on both pages, the 2.x figure per-prompt on the Gemini page (the Vertex page's 2.x wording
is **UNVERIFIED** here).

Response fields (read from google-genai `GroundingMetadata` and ADK `LlmResponse.grounding_metadata`):

- **Per grounded prompt (2.x):** the call counts once if its `grounding_metadata` carries a Google Search
  signal: non-empty `web_search_queries` (primary) or `grounding_chunks` with a `web` source. Count calls,
  not queries. The adapter already groups a streamed call's chunks and ORs the flag over the group
  (`build_session_digest`), which is what makes "metadata on a partial chunk only" count once.
- **Per query (3.x):** `sum(len(web_search_queries))` over the call (over the group, taking the max across
  chunks, as #86 does for the count). **UNVERIFIED:** that the number of entries in `web_search_queries`
  equals the number of billed "search requests" (the suite's G2/G3 arithmetic assumes it; nobody has
  compared a real response to an invoice).
- **Which family:** decided by the price-table entry the model resolves to (section 5), not by
  string-prefix logic in code, so a new model that ships with a grounding row is data, not a code change.
- **Not priced in v1, flag stays:** `image_search_queries` (price UNVERIFIED), `google_maps_widget_context_token`
  / Maps chunks (Maps prices were read in an earlier session but are not in the suite's recorded sources:
  UNVERIFIED), `retrieval_queries` / Vertex AI Search chunks (a different fee, and the field is documented
  "not supported in Gemini API").

Note for #86's own wording: `_grounding()` currently flags on `grounding_chunks` alone, which also fires for
Vertex AI Search and Maps sources. The message says "grounding fee", which is correct in kind but not in
which fee. The priced path must therefore distinguish the source (web vs maps vs retrieval) and leave the
rest flagged.

## 2. The free allowance

Published: 1,500 prompts/day (2.x) and 5,000 requests/month (3.x) free (suite `cases.py` header). A plugin
sees one call at a time; it cannot know the account's usage that day or month, the project's other
services, or whether the allowance is shared across models (**UNVERIFIED**).

Options:

1. **Price at the paid rate, state the assumption** in the output. The figure is an **upper bound** on the
   grounding component (a user inside the allowance pays $0).
2. **Report the fee as a separate upper bound**, keep the token total exact, and never add them.
3. **Assume the free tier** (fee = $0) and say so. Silent under-count for every user past the allowance;
   this is the failure #86 exists to remove.

**Recommendation: option 1, with option 2's separation of the number.** Reasons: it matches what the suite
already scores as PASS (G1-G3 price past the allowance and say so), it errs in the direction a budget
tolerates (over, never under), and it is not silent because every output that includes a grounding fee
prints the assumption. Concretely:

- The fee is added to the invocation's `cost_usd` at the paid rate.
- Text report: a line `  includes grounding fees at the paid rate ($X of the total); your free allowance
  (1,500 prompts/day on 2.x, 5,000 requests/month on 3.x) would lower this`.
- `--json`: `grounding_fee_usd` (per invocation and total), `grounding_fee_basis:
  "paid_rate_upper_bound"`, and `total_excluding_grounding_usd` so a free-tier user can read the exact token
  total without arithmetic.
- Opt-in for users who know they are inside the allowance: `ADK_TRACEGAUGE_GROUNDING_FREE_ALLOWANCE=1`
  prices the fee at $0 and prints "grounding priced at $0 because ADK_TRACEGAUGE_GROUNDING_FREE_ALLOWANCE
  asserts it is inside the free allowance" (same shape as `ADK_TRACEGAUGE_ASSUME_LOCAL`: an explicit,
  printed assertion rather than a default).

**Required (owner correction, 2026-09-21): `total_is_upper_bound`, not `total_is_complete` staying true.**
When any grounding fee is priced at the paid rate the output carries an explicit, separate field and text:

- `--json`: `total_is_upper_bound: true` (top level; also `is_upper_bound` per invocation), alongside
  `total_is_complete`. `total_is_complete` keeps its #86 meaning ("nothing was left out"), so it is `true`
  when every billed component was priced; it does NOT mean the figure is exact. A consumer that wants "is
  this number exact" reads `total_is_complete and not total_is_upper_bound`. With the opt-in env var below
  (fee asserted at $0), `total_is_upper_bound` is `false`.
- Text report: the total line must read exactly
  `upper bound (grounding priced at paid rate; free allowance not observable)`, e.g.
  `  total: $0.038000 across 1 invocation(s) -- upper bound (grounding priced at paid rate; free allowance not observable)`.
  The "true total is at least this" (lower-bound) wording must not appear on this path, and the two
  labels must never both be printed for the same total: if something else is still unpriced or unknown
  the total line says which lower-bound reason applies and is also marked upper-bound for the grounding
  part ("at least the unpriced components, at most the paid-rate grounding").
- Rows with a priced grounding fee carry a distinct marker from the `*` (unpriced) marker, e.g. `~`, and the
  per-row detail names the fee.
- The `check` regression gate and any consumer that sums `total_usd_priced` must treat an upper-bound total
  as such; the field exists so that they can.

## 3. Vertex vs Gemini API

Published (Vertex page, read in an earlier session; **UNVERIFIED in the suite's recorded sources**):
Vertex bills grounding only when the response returns sources. The Gemini API page's wording on whether a
prompt with zero results is billed is also UNVERIFIED. **Real-response finding:** "sources returned" cannot
be read from `grounding_chunks`: on the Gemini API two calls (A and F) answered from search results with no
chunks at all, and a zero-result prompt (D) carried `web_search_queries` and no chunks either, so the two
cases look identical by chunks. Whatever a Vertex rule needs, `grounding_chunks` is not a sound signal for it
(Vertex response shape UNVERIFIED), which is one more reason v1 leaves Vertex flagged.

Telling the backend apart, in order of reliability:

1. `agent.canonical_model.api_client.vertexai` on ADK's `Gemini` class (`google_llm.py`: `_api_backend` is
   `VERTEX_AI if self.api_client.vertexai else GEMINI_API`, read from source). Reachable from
   `callback_context` only through ADK internals (`_invocation_context.agent`), so **UNVERIFIED** as stable
   across google-adk 2.6-2.9 and for every path (`adk eval`, sub-agents via `AgentTool`, live mode).
2. `usage_metadata.traffic_type`, documented "not supported in Gemini API": a non-null value implies Vertex.
   A positive signal only (null does not prove Gemini API); **UNVERIFIED** that Vertex always populates it.
3. `GOOGLE_GENAI_USE_VERTEXAI` env var (`env_utils.py`): fallback, wrong when a caller passes a
   `Client` explicitly.

**Fail-closed rule:** price only when source 1 positively reports the Gemini API. Absence of `traffic_type`
or of the env var is not evidence of the Gemini API. If the backend cannot be identified, or is Vertex, the
#86 flag stays. v1 does not price the Vertex backend: that needs its own parsed rate and its
"sources returned" condition (chunks non-empty), estimated +1 day and a Vertex-page parser in the checker.
Also **UNVERIFIED:** that the price table's Gemini token rates equal Vertex's; the table has always been the
Gemini API rates and Vertex-routed Claude/GPT are documented as out of scope.

## 4. Interaction with the #86 flag

State per grounded call, evaluated in `_adapter.build_session_digest`:

| Situation | Result |
|---|---|
| Google Search signal, model resolves to an entry with a `grounding.google_search` row, backend = Gemini API | fee priced (section 1), added to the turn's cost; **`grounding_fee` is removed from `unpriced_components`**, an `assumptions` entry is added, and `total_is_upper_bound` is set (section 2). `total_is_complete` reports only that nothing was left out. The call's tool-use tokens must be resolved first (findings above), else the invocation is still `unknown` |
| Model has no grounding row (any entry we have not verified) | flag stays |
| Backend unknown or Vertex | flag stays |
| Maps / image search / retrieval signal | flag stays, message says which source |
| Mixed (one call priceable, one not) | the priceable fees are priced; the invocation keeps a `grounding_fee` unpriced component for the rest |

Consequences to implement deliberately:

- `_cost.py` stays pure token arithmetic (it is documented as a port kept close to its origin). The fee is
  a per-turn extra carried on `AdaptResult` (e.g. `fees: tuple[Fee, ...]` with `turn_index`, `agent_name`,
  `usd`) and added in `price_digest`, the single sanctioned call site; `test_pricing_call_site` must keep
  passing. `cost_by_agent` gets the fee under the grounded call's agent, so the per-agent sum still equals
  `cost_usd`.
- Snapshot schema 4 -> 5, additive (`assumptions`, `grounding_fee_usd`, `is_upper_bound`); v1-v4 still read.
- The eval metric scores a priced-with-assumption invocation and puts the assumption in the rationale;
  it returns `NOT_EVALUATED` only while the flag stays.
- The regression gate (`check`) compares totals: mixing pre- and post-change snapshots of a grounded agent
  would show a jump. Needs a CHANGELOG note and, cheaply, a `check` warning when the two snapshots
  differ in whether grounding was priced (**design decision, not free**).

## 5. Price-table schema and the vendor check

Table `schema_version` 3 -> 4, additive per model entry:

```json
"grounding": {
  "google_search": {
    "unit": "per_grounded_prompt",
    "usd_per_1k": 35.0,
    "free_allowance": {"count": 1500, "period": "day"}
  }
}
```

`unit` is `per_grounded_prompt` or `per_search_query`; a model without the block has no priced grounding
(flag stays). It shares the entry's `fetched_on`, so the existing 30-day freshness gate covers it.

`scripts/check_price_table_vs_vendor.py` today parses only `Input price` / `Output price` / `Context caching
price` rows (`parse_google_html`, `_GoogleTable`, `_parse_cell`). Extension:

- Capture rows whose label starts with `Grounding with Google Search` (label text **UNVERIFIED**: the
  parser has never looked at that row; the first task is to read the live HTML and fix the label).
- New cell parser: `\$([\d.]+)\s*/\s*1,000\s*(grounded prompts?|search (?:queries|requests))` for rate and
  unit, and a free-allowance regex for `1,500 ... per day` / `5,000 ... per month`.
- Same status semantics as the promo window: entry carries a `grounding` block and the page shows the same
  unit, rate and allowance -> `VERIFIED`; disagree -> `MISMATCH`; page row missing or unparseable ->
  `UNVERIFIED` (fails, never skips); page lists a grounding fee the entry does not carry -> `MISMATCH`
  (forces the table to stay complete, exactly as an un-carried promo window does).
- Vertex page: not parsed in v1 (Vertex keeps the flag), so no Vertex row is claimed as verified.
- Fixture tests use captured HTML in `tests/test_check_price_table_vs_vendor.py`, like the existing ones.

Risk: Google's page layout for these rows is the least stable part of the plan. The checker turns layout
drift into a red weekly run and a blocked release (by design), which is the intended failure mode but costs
maintenance.

## 6. Test plan

Suite (`adk-cost-correctness`), rule: new cases committed BEFORE scoring, results not adjusted:

- **Flip INCOMPLETE -> PASS on the priced path:** G1 ($0.036350), G2 ($0.047700), G3 ($0.025580), because
  the reference figures are already the paid-rate figures. G3's sub-agent path is priced on the sub-agent's
  own invocation (that is where the metadata is). **Caveat from the real captures:** these three fixtures set
  no `tool_use_prompt_token_count`, real responses always do, so they flip only for a fixture that is not
  what Gemini returns. Do not change G1-G3 (rule: cases are not adjusted after scoring); add realistic
  twins instead, committed before any scoring:
  - G1r / G3r: the same scenarios with `tool_use_prompt_token_count` of about 120-150 (the observed range),
    expected figure = tokens + tool-use tokens priced per the chosen option (a) + the paid-rate fee. Today
    every tool scores these INCOMPLETE/unknown or FAIL, which is the honest current state.
  - G10: a grounded call that returns zero results (metadata with queries and no chunks): still one fee
    under the recommended paid-rate rule (billing of zero-result prompts is UNVERIFIED, so the case's
    `sources` must say the expectation is the conservative reading).
- **New cases needed:**
  - G4: an agent loop with two grounded 2.5 calls -> 2 x $0.035 (per prompt, not per query).
  - G5: streamed grounded call, metadata only on a partial chunk -> one fee, not zero and not two.
  - G6: Gemini 3.x with `grounding_chunks` but empty `web_search_queries` -> what is billed is unknown, so
    INCOMPLETE flagged (kind "cost", declared incomplete figure = tokens only).
  - G7: Vertex-backend call (`traffic_type` set) -> INCOMPLETE flagged.
  - G8: Maps or retrieval grounding source -> INCOMPLETE flagged, not priced as a Google Search fee.
  - G9: free-allowance opt-in env var set -> fee $0 and the assumption printed (checks the assumption is
    visible, not just the number).
- The in-repo `ReferencePlugin` already encodes per-prompt vs per-query; extend it for G4-G9 so the suite is
  satisfiable, and keep the naive plugin failing them.

adk-tracegauge unit tests: per-family arithmetic with hand-computed figures; streamed dedupe; per-agent
attribution sums to `cost_usd`; backend detection (mock `api_client.vertexai`, `traffic_type`, env); each
"flag stays" row of section 4; text and `--json` output including the assumption line; snapshot v4 file
reads; eval rationale; checker parser fixtures for each status.

**Riskiest assumption first (rule 77):** capture one real grounded response each on a 2.5 and a 3.x model
(needs a Gemini API key and consumes quota or costs up to $0.035 + $0.014 + tokens) and confirm (a) that
`web_search_queries` length is what is billed, (b) the metadata shape, (c) whether a zero-result prompt is
billed. This needs your approval and a key; nothing here has been checked against a real response.

## 7. Effort and what I am unsure about

Estimate (mine, not measured): table + schema 0.5 day; plugin/adapter/report/JSON/eval 1 day, **plus 0.5 day
for resolving the tool-use tokens (findings above) and 0.25 day for `total_is_upper_bound` and its text**;
checker extension 1 day (mostly reading the live HTML and stabilising the parser); tests + suite cases
(including the realistic twins) 1 day; CHANGELOG/README/release 0.25 day. **About 4.5 days for the Gemini
API path**, plus about 1 day for Vertex. Gemini 3.x needs a paid-tier capture first (the free-tier key
cannot obtain one); if that capture contradicts an assumption below, add up to a day.

Status of every assumption after the 2026-09-21 real captures:

| # | Assumption | Status |
|---|---|---|
| 1a | `len(web_search_queries)` equals billed requests (3.x) | **UNVERIFIED** (free tier: nothing invoiced; 3.x grounded calls return 429 on this key) |
| 1b | "One charge per grounded prompt" (2.x) holds in agent loops | Structure **VERIFIED** (n=1: 5 model calls, exactly 2 grounded, one per sub-agent call); the billing side **UNVERIFIED** |
| 2 | A zero-result prompt is / is not billed | Metadata presence **VERIFIED** (n=1: queries present, no chunks); billing **UNVERIFIED** |
| 3 | Free allowance unobservable to a plugin | **VERIFIED that nothing in the response shows it** (no usage/quota field in any captured response); its scope (per project/model, shared or not) **UNVERIFIED** |
| 4 | Vertex per-prompt wording, "billed only when sources return", Vertex rates equal the table | **UNVERIFIED** (no Vertex access); the chunk-based reading of "sources returned" is **refuted** on the Gemini API |
| 5 | Backend readable from ADK internals | **VERIFIED** on google-adk 2.9.2 / Gemini API for root and sub-agent calls; other versions, `adk eval`, Vertex, live mode **UNVERIFIED**; `traffic_type` on Vertex **UNVERIFIED** |
| 6 | The pricing page has a parseable `Grounding with Google Search` row | **UNVERIFIED** (page not re-read, per the design-only brief) |
| 7 | Maps / image-search / retrieval out of v1 | Unchanged decision; their prices remain outside the recorded sources |
| 8 | Gemini 3 grounding billing began 2026-01-05, rate may move again | **UNVERIFIED** (earlier-session reading) |
| 9 | Upper-bound framing is the right product call | Judgement, not a fact; now expressed as `total_is_upper_bound` plus the exact text label (section 2) so it is never mistaken for an exact total |
| 10 (new) | Tool-use tokens on grounded calls: billed? at what rate? | **UNVERIFIED**; observed on 6 of 6 real 2.5 grounded calls, and they are what currently makes such invocations `unknown` |
| 11 (new) | Search grounding on Gemini 3.x is unavailable to free-tier keys | **UNVERIFIED** cause; observed: 429 on all four 3.x models tried |
