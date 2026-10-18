---
version: 1
slug: "web-src-app-page-tsx"
primary_target: "web/src/app/page.tsx"
related_targets: []
---

# Surface brief: placeholder home (`/`)

Scope: walking-skeleton placeholder at https://disclosly.com. Mode: Persuade (light): the visitor learns what Disclosly is and sees that it is alive.
Audience: Turkish retail investors first; English-speaking reviewers second.
Content: fixed by specs/001-walking-skeleton/contracts/web-page.md (name, tagline, disclaimer, backend status, TR/EN toggle). No claims beyond it.
Constraints: 360 px, WCAG AA, no new dependencies.
History: round 1 "certified file" was built and rejected by the owner (read as AI-made: card layout, colors). Re-roll round 1 chose the trading-hall split-flap board.

## Direction contract

THESIS: the page is the departures-style quote board of a trading hall, not a card on a background. It refuses the centered card, the soft shadow and the hairline form box.
OWN-WORLD: the board fills the viewport: matte graphite housing, flap tiles a step lighter, each split by a hinge line; letters in warm off-white condensed caps; one enamel amber reserved for the live row; a printed enamel plate for the legal notice. No glow, no gradient, flat paint only.
STORY: the visitor reads DISCLOSLY on the flaps, the tagline on the placard beneath, watches the status row flip to its state and version, reads the plate, and flips the language like turning the board to its English side.
FIRST VIEWPORT: top strip: a small enamel sources plate (KAP · SEC EDGAR) left, language key right. Name row of 9 flaps spanning the content width. Tagline placard. Status row: 12 flaps for the state plus 7 for the version, with the plain sentence under it. Enamel plate at the bottom edge.
FORM: IMPECCABLE'S PICK from re-roll round 1 of seed 21050b83 (the re-roll printed the same key; trading-hall split-flap board). Signature interaction: flaps cycle through characters and settle left to right when the state or language changes; reduced motion sets them at once.
FINISH: unreviewed and unshipped until the finish reviewer returns ship.
