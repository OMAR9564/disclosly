---
version: 1
slug: "web-src-app-page-tsx"
primary_target: "web/src/app/page.tsx"
related_targets: ["web/src/app/runs/[id]/page.tsx"]
---

# Surface brief: home and ask flow (`/`, `/runs/[id]`)

Scope: the home board, now carrying the Phase 3 ask flow (T023). Mode: Operate: the signed-in visitor asks a question and checks every figure against its source.
Audience: Turkish retail investors first; English-speaking reviewers second.
Content: question box, live run timeline (sse-events.md), answer (summary with [n] markers, points, citations), partial / refused / no_evidence / error states, disclaimer. Placeholder content (name, tagline, status) from specs/001 stays. No claims beyond the API's data.
Success: figures plus their sources, one click from figure to excerpt. Untouchable: the disclaimer plate is always visible at the board's bottom.
Constraints: 360 px, WCAG AA, keyboard-operable, no new dependencies.
History: round 1 "certified file" rejected; re-roll round 1 chose the trading-hall split-flap board (placeholder). Ask-flow surface round (seed b5c9a930) locked the Annunciator Board.

## Direction contract

THESIS: the run is a hall annunciator, not a chat. Stage cells report the agent's work; the answer lands as arrival rows, each figure carrying its source code like a flight number. It refuses message bubbles, cards and spinners.
OWN-WORLD: DESIGN.md unchanged: graphite housing, hinged flap tiles, warm letter caps, one amber for the live cell only, cream enamel plates for sources and the legal notice, flat paint.
STORY: the visitor types on the question placard, presses the SOR key, watches ARAMA · RAKAM · MODEL · KONTROL flip from — to counts with the active cell amber, reads the summary placard, scans arrival rows (figure on flaps, label, source code), opens a row's source plate to read the excerpt, follows the source link.
FIRST VIEWPORT: top strip; name row; question placard with input and SOR key; 4-cell stage row; summary placard; arrival rows; disclaimer plate at the bottom edge. On `/runs/[id]` the name row is dropped for a small DISCLOSLY plate (it frees the first viewport for the answer); question set as text, stream replayed; on a finished run the step log collapses and the arrival figures are the largest flaps.
FORM: Annunciator Board, position 6 on the ordered list, surface seed b5c9a930, code-led. Signature interaction: stage cells spin and settle as events arrive; a figure's flaps spin in when the final answer lands; reduced motion sets them at once.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
