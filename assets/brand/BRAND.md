# adk-tracegauge brand system — Calibration

Shares the Calibration identity with the sibling package `tracegauge`
(`token-efficiency-scorer`'s `assets/brand/BRAND.md` is the canonical
palette/type reference — this file documents only what's specific to
this package: the motif and this repo's own asset list).

## Why a different motif, not a recolored gauge

`tracegauge` reports a continuous reading against a calibrated band — the
gauge arc (threshold band + tick ring + needle) is the right shape for
that. `adk-tracegauge` doesn't report *where* on a continuum a run sits —
it reports whether a run clears a line. A gate, not a gauge: binary, not
graduated. Drawing it as a small dial would misrepresent what this
package actually measures, so the motif is deliberately arc-free and
tick-free — a track, a single threshold line, and a marker on the pass or
fail side.

## Color

Same seven Calibration tokens as `tracegauge` (see that repo's
`BRAND.md` for the full table and the reasoning behind each). This
motif uses four of the seven directly:

| Token | Hex | Role in this motif |
|---|---|---|
| `paper` | `#F0EDE4` | The gate line itself, the wordmark |
| `calibrated` | `#4F7A5C` | The marker, when it's past the gate on the pass side |
| `graphite` | `#5B5D53` | The track |
| `tick` | `#A79F8C` | The motion trace and its origin point, the tagline |

(`needle` and `regression` are reserved for a future failing-run variant
of this same motif — the marker would sit before the gate, in
`regression` red, rather than past it in `calibrated` green. Not built
here; this asset shows the passing case.)

## Type

Same as `tracegauge`: Space Grotesk 700 for the wordmark, IBM Plex Sans
400 for the tagline. Both open (SIL OFL), bundled locally in
`assets/brand/fonts/` (copied from `token-efficiency-scorer`'s own
bundled copies — same license, same files, no CDN dependency).

## Assets in this directory

- `fonts/spacegrotesk-700.woff2`, `fonts/ibmplexsans-400.woff2` — the two
  weights this package's hero image needs. Subsetted to alphanumerics +
  space only (no punctuation at all, not even a hyphen) — see
  `scripts/generate_og_preview.py`'s own docstring for how the hyphen in
  "adk-tracegauge" itself is handled (hand-drawn, not a font glyph).
- `og-preview.svg` — 1280×640 link-unfurl preview (GitHub/Slack/social).
  Direction B composition: motif left (dominates), outlined wordmark +
  tagline right. Every character is a real path traced from this
  directory's own font files via `fontTools`, never a live `<text>`
  element — this rasterization pipeline's SVG renderer does not do real
  font-family matching (proven in `tracegauge`'s AU1 rasterization:
  identical `font_extents` regardless of the requested font name), so
  text is a shape problem here, not a typography problem.
- `og-preview.png` — 1280×640 PNG export of the above, for repo Settings
  → General → Social preview (GitHub serves this PNG directly; it
  doesn't render the SVG). RGB truecolor, 8-bit, no alpha, no
  interlacing, no ICC profile, exactly `IHDR+IDAT+IEND` — the
  maximally-conservative PNG structure, by construction, matching the
  structure `tracegauge`'s own sibling asset uses after its AU1/BR2/BS1
  404 investigation.

Regenerate via `scripts/generate_og_preview.py` (see that file's
docstring for the throwaway-venv setup — never run design tooling
against this package's own dev environment).

Not yet done: a favicon, wordmark lockup, or README badge for this
package — this wave built only the hero/social-preview image.
