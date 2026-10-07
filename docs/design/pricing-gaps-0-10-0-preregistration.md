# 0.10.0 pricing gaps: pre-registered expectations (written before implementation, 2026-10-07)

Baseline: adk-tracegauge 0.9.1 on the 15-case `adk-cost-correctness` suite scores
6 PASS / 9 INCOMPLETE (G1, G2, G3, G1r, G2r, G3r, T1, A1, M1) / 0 FAIL.

Vendor sources (raw `curl`, fetched 2026-10-07T12:25:53Z; SHA-256 prefixes in the price table).

## Expected outcome per case

| Case | Expected before | Expected after | Why |
|---|---|---|---|
| A1 (gemini-2.5-flash, 200 text + 1,000 audio in, 100 out) | INCOMPLETE | PASS, $0.001310 | audio $1.00/M published for 2.5-flash; 200 x 0.30 + 1,000 x 1.00 + 100 x 2.50 per M |
| M1 (gpt-4o, 1M in, 100k out) | INCOMPLETE | PASS, $3.50 | gpt-4o is in the table with the published rates 2.50 / 1.25 cached / 10.00 |
| G1 (2.5 grounded, Gemini API) | INCOMPLETE | PASS, $0.036350 | $35 / 1,000 grounded prompts at the paid rate |
| G1r (replay, real capture) | INCOMPLETE | PASS, $0.0361907 (also accepted: $0.0362264 with tool-use tokens at the input rate) | same |
| G3r (replay, real capture) | INCOMPLETE | PASS, $0.0371853 (also accepted: $0.0372249) | same |
| G2, G3, G2r | INCOMPLETE | INCOMPLETE | Gemini 3.x grounding is not priced (no captures; paid tier required) |
| T1 | INCOMPLETE | INCOMPLETE | out of scope (not a price-table gap) |

Expected total: **11 PASS / 4 INCOMPLETE / 0 FAIL**. Any case that moves PASS -> INCOMPLETE/FAIL is a
regression and stops the release. Grounded totals carry the label
"upper bound (grounding priced at paid rate; free allowance not observable)".

## Not priced (stay flagged)
Gemini 3.x grounding, Vertex grounding, any model with no published audio rate (audio stays flagged),
Maps grounding.

## Amendment (written after implementation, before the final scoring run)

The table above assumed the suite's replay model lets a tool read the backend. It does not: the replay
model was a bare `BaseLlm` with no `api_client`. A tool that prices a grounding fee only after positively
identifying the Gemini API backend (the design's fail-closed rule) therefore cannot be scored on G1, G1r
and G3r whatever it does. Result of the dev build against the UNMODIFIED suite (suite `eac41ec`), recorded
before the suite was touched: **8 PASS / 7 INCOMPLETE / 0 FAIL** (A1 and M1 pass; every grounding case
stays flagged). The suite harness is changed so `gemini-*` replay models subclass ADK's own `Gemini`
class (suite `72ca8da`). That change was checked to move no existing result (adk-tracegauge 0.9.1 stays
6 / 9 / 0, adk-finops 0.6.1 stays 4 / 5 / 6) and is disclosed in the scorecard. The expectations above are
unchanged.
