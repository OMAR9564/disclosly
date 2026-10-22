---
name: Disclosly
description: Cited research over KAP and SEC EDGAR disclosures, shown as a trading-hall split-flap board.
colors:
  housing: "#181817"
  flap: "#2b2b29"
  flap-hover: "#343432"
  flap-low: "#1e1e1d"
  hinge: "#0b0b0a"
  letter: "#efe9dc"
  letter-muted: "#b9b3a7"
  amber: "#f2a51f"
  plate: "#efe9dc"
  plate-ink: "#16181b"
  rule: "#383835"
typography:
  display:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "calc(100cqi / var(--n) * 1.1)"
    fontWeight: 700
    lineHeight: 1
    fontVariation: "\"wdth\" 68"
  headline:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "clamp(1.125rem, 2.8vw, 1.625rem)"
    fontWeight: 500
    lineHeight: 1.35
  title:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 800
    letterSpacing: "0.02em"
    fontVariation: "\"wdth\" 85"
  body:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 600
    letterSpacing: "0.08em"
    fontVariation: "\"wdth\" 80"
  button:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 700
    letterSpacing: "0.04em"
    fontVariation: "\"wdth\" 80"
  label-small:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    letterSpacing: "0.08em"
    fontVariation: "\"wdth\" 80"
  caption:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 600
    lineHeight: 1.35
  figure:
    fontFamily: "Archivo, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "\"tnum\" 1"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "0.9375em"
rounded:
  flap: "clamp(2px, 0.5cqi, 5px)"
  key: "3px"
  plate-sm: "6px"
  plate-lg: "8px"
spacing:
  board-pad: "clamp(1rem, 4vw, 3.5rem)"
  board-gap: "clamp(2rem, 6vw, 4.5rem)"
  name-gap: "clamp(1rem, 3vw, 1.75rem)"
  row-gap: "0.75rem"
  tile-gap: "clamp(2px, 0.6cqi, 6px)"
  board-split: "clamp(0.5rem, 2vw, 1.5rem)"
  band-gap: "1rem"
  step-pad: "0.875rem"
  cell-pad: "0.625rem 0.75rem"
  table-gap: "2rem"
components:
  flap-tile:
    backgroundColor: "{colors.flap}"
    textColor: "{colors.letter}"
    typography: "{typography.display}"
    rounded: "{rounded.flap}"
  flap-tile-live:
    backgroundColor: "{colors.flap}"
    textColor: "{colors.amber}"
    typography: "{typography.display}"
    rounded: "{rounded.flap}"
  button-key:
    backgroundColor: "{colors.flap}"
    textColor: "{colors.letter}"
    typography: "{typography.button}"
    rounded: "{rounded.key}"
    padding: "0.5rem 1rem"
    height: "2.75rem"
  button-key-hover:
    backgroundColor: "{colors.flap-hover}"
  button-key-primary:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.plate-ink}"
    typography: "{typography.button}"
    rounded: "{rounded.key}"
    padding: "0.5rem 1.25rem"
    height: "2.75rem"
  button-key-quiet:
    textColor: "{colors.letter}"
    typography: "{typography.button}"
    rounded: "{rounded.key}"
    padding: "0.5rem 1.25rem"
    height: "2.75rem"
  button-key-quiet-hover:
    backgroundColor: "{colors.flap}"
  plate-sources:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.plate-ink}"
    rounded: "{rounded.plate-sm}"
    padding: "0.375rem 0.75rem"
  plate-legal:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.plate-ink}"
    rounded: "{rounded.plate-lg}"
    padding: "1.25rem 1.5rem"
  plate-ticker:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.plate-ink}"
    typography: "{typography.label-small}"
    rounded: "{rounded.plate-sm}"
    padding: "0.2rem 0.5rem"
  placard:
    textColor: "{colors.letter}"
    typography: "{typography.headline}"
    padding: "1rem 0"
  plan-step:
    textColor: "{colors.letter}"
    typography: "{typography.body}"
    padding: "0.875rem 0"
  plan-field:
    backgroundColor: "{colors.flap}"
    textColor: "{colors.letter}"
    typography: "{typography.body}"
    rounded: "{rounded.key}"
    padding: "0.5rem 0.75rem"
    height: "2.75rem"
  report-table:
    textColor: "{colors.letter}"
    typography: "{typography.figure}"
    padding: "{spacing.cell-pad}"
  report-table-head:
    textColor: "{colors.letter-muted}"
    typography: "{typography.label-small}"
    padding: "{spacing.cell-pad}"
  chart:
    backgroundColor: "{colors.housing}"
    textColor: "{colors.letter-muted}"
    typography: "{typography.caption}"
    rounded: "{rounded.key}"
    height: "260px"
---

# Design System: Disclosly

## Overview

**Creative North Star: "The Trading-Hall Board"**

The whole viewport is the housing of a departures-style quote board in a dim trading hall. Words that matter are set on split-flap tiles: graphite leaves a step lighter than the housing, each cut across the middle by a black hinge, carrying warm off-white condensed capitals. Fixed information that a real board would carry as printed signage (the sources, the legal notice) sits on cream enamel plates with an inked border ring. Everything else is plain lettering on the housing, divided by thin rules. Figures read like a quote board: label-style column heads, tabular numerals set right, rules between rows.

The board is a physical object, so it is flat paint only: no glow, no gradient, no soft drop shadow, no floating card. Depth comes from the two-tone flap leaves, the hinge line and inset enamel rings, never from light. It keeps one dark palette regardless of the visitor's color scheme. The one moving part is the flap: when a value changes, the tiles spin through characters and settle left to right; under reduced motion they set at once. When the board needs a decision, the decision sits on the board as a band under the row it pauses, never in a modal, and the one action to take is the only enamel plate that moves.

Density is low and the scale is large. The name row spans the full content width, and each flap row sizes its tiles from its own width, so the board reads the same at 360 px and at 1440 px.

**Key Characteristics:**
- Matte graphite housing, flap tiles a step lighter, a hinge across every tile.
- Warm off-white condensed caps (Archivo, width axis 68 to 85).
- One enamel amber, reserved for the live row.
- Cream enamel plates with inset ink rings for fixed signage.
- Flat paint; depth is built from leaves, hinges and inset rings.
- Flaps spin and settle left to right on change; reduced motion sets them at once.
- Charts drawn in the board's own inks: series differ by dash and marker shape, never by hue.
- One cream push plate per decision: the single action to take.

## Colors

A near-monochrome warm graphite board with one enamel amber and cream enamel plates.

### Primary
- **Enamel Amber** (`amber`): the letter color of the live flap row only: the "available" state word, the stage cell that is working right now, and, while a run waits on the visitor, the plan's decision word ("KARARIN" / "YOUR CALL", or "HAK BİTTİ" / "NO RUNS" on the deep quota offer). No stage is live while the run waits, so the decision word is the one live row. It marks the one value on the board that is currently true and working.

### Neutral
- **Matte Graphite Housing** (`housing`): page background, the board itself, and the browser theme color.
- **Flap Graphite** (`flap`): upper leaf of every flap tile, upper leaf of blank-flap fields, the face of every board key, the hover face of the quiet key, the chart's focus column and the ground of the chart hatch.
- **Pressed Flap** (`flap-hover`): a key's face on hover; the fourth bar fill in a chart.
- **Lower Leaf** (`flap-low`): the lower half of each tile and of each blank-flap field, one step darker than the upper leaf.
- **Hinge Black** (`hinge`): the hinge line across each tile and the bottom lip of keys and fields.
- **Warm Letter White** (`letter`): flap letters, body text, focus ring, selection fill, the 1px rule beside a reason or error, the first chart ink and the first bar fill.
- **Faded Letter** (`letter-muted`): row labels, column heads, the plain sentence under a flap row, chart axis text, the chart's zero line, the second chart ink and the second bar fill, and the lip of the push plate.
- **Enamel Cream** (`plate`): the face of the enamel plates, ticker plates and the push plate. Same value as Warm Letter White, but a separate role: plate surface, not ink.
- **Plate Ink** (`plate-ink`): text on plates and the plates' inner border ring.
- **Housing Rule** (`rule`): 1px dividers under the top strip, around the tagline placard, above the plan band, between plan steps and table rows, and the chart's grid lines.

### Named Rules
**The One Amber Rule.** Amber colors the letters of the live row and nothing else: no amber buttons, links, borders or backgrounds. If two things are amber, one is wrong.

**The One Board Rule.** The palette does not invert for light mode (`color-scheme: dark`). A board in a hall does not change paint with the visitor's settings.

**The Ink, Not Hue Rule.** Charts use the board's inks and nothing else. Line series are told apart by dash pattern (solid, 7 5, 2 4, 10 4 2 4) and marker shape (square, circle, diamond, triangle), alternating Warm Letter White and Faded Letter ink. Bar series fill Warm Letter White, Faded Letter, a 45° letter hatch on flap, then Pressed Flap. A fifth series repeats the cycle; it never gets a new color.

**The No Alarm Rule.** Errors and reasons are not red. An error or a per-field reason is Warm Letter White text beside a 1px Warm Letter White rule on its left; an invalid field adds a 1px letter ring inside its hinge lip.

## Typography

**Display Font:** Archivo (variable width axis), with system-ui, sans-serif
**Body Font:** Archivo, with system-ui, sans-serif
**Label/Mono Font:** ui-monospace, SFMono-Regular, Menlo, monospace (commit hashes only)

**Character:** One grotesque family stretched across its width axis: tight condensed caps on the flaps, slightly wider condensed caps on plates and keys, normal width for reading text. The width axis carries the hierarchy as much as size does.

### Hierarchy
- **Display** (700, width 68, tile height times 1.1, line-height 1): flap letters. Size is computed from the row's own width divided by its tile count, so it is never set in rem.
- **Headline** (500, clamp(1.125rem, 2.8vw, 1.625rem), 1.35, balanced wrap): the tagline placard under the name row.
- **Title** (800, width 85, 1.125rem, 0.02em, uppercase): the heading of the legal plate.
- **Body** (400, 1rem, 1.5, max 60ch on the board, 72ch on plates at 0.9375rem): plain sentences under rows and plate body text.
- **Label** (600, width 80, 0.875rem, 0.08em, uppercase, faded letter): the label naming a flap row, like a column header on a real board. The sources plate uses the same width at 700.
- **Label Small** (600, width 80, 0.75rem, 0.08em, uppercase, faded letter): table column heads, plan field labels and the step kind. Chart series names use it at 0.06em. Plan meta terms sit between the two label sizes at 0.8125rem.
- **Caption** (600, 1.0625rem, 1.35, balanced wrap): the title of a report table or chart. Plan step goals take the same size at 400 and 1.4.
- **Figure** (400, 1rem, tabular numerals): report table cells, quota counts, plan meta values and the chart readout (600, 1.0625rem). Numeric columns align right.
- **Button** (700, width 80, 0.9375rem, 0.04em): the language key; board keys add uppercase.

### Named Rules
**The Width Axis Rule.** Condensing is done with Archivo's `wdth` axis (68 flaps, 80 labels and keys, 85 plate titles), never with a second condensed family or with negative tracking.

**The Same-Text Rule.** Flap rows are decorative (`aria-hidden`); the same value always exists as real text nearby (a screen-reader name or the plain sentence under the row). A chart is the same: the plot is hidden, a screen-reader table carries every value, and the plot's slider value text names the focused point.

**The Quote Board Figures Rule.** Every figure is set in tabular numerals; in a table, numeric columns align right and the heads read like a board's column labels. Figures never get a display size of their own; a figure that matters goes on a flap row.

## Layout

The board is a single centered column (max 76rem) filling at least the full dynamic viewport height, with fluid padding (`board-pad`) and fluid gaps between its bands (`board-gap`). The bands stack in fixed order: top strip (sources plate left, language key right, rule beneath), name block, flap rows, and the legal plate pushed to the bottom edge by a flexible last track.

Flap rows are grids of equal tiles (`repeat(n, 1fr)`, 3:4 aspect, `tile-gap`) inside an inline-size container, so tile size, gap, radius, hinge and letter size all scale with the row's width. The status row places a 12-tile state row and a 7-tile version row side by side at 12fr / 7fr (max 52rem); at 30rem and below they stack and the version row shrinks to 58% width. Readable text is capped at 60ch on the board and 72ch on plates.

Decision and report bands follow the same grammar: a row label and a flap row on one line, a plain sentence, then rows divided by 1px rules. The plan band opens with a rule and 1.25rem of top padding under the stage row; its parts stack at `band-gap`. Each plan step is a two-column row (a 1.75rem single-tile step number, then the body) with `step-pad` above and below. The confidence row sets label, six-tile flap row (7rem to 12rem) and note side by side. Report tables stack at `table-gap`; each table sits in its own horizontal scroll frame and is at least min(100%, 36rem) wide. The chart is 260px tall and as wide as its column (never under 240px), with its readout (an 8-tile flap row of the focused x value, then each series' value) above the plot.

At 36rem and below: plan step pairs and the confidence note stack, every plan key takes the full width, table cells tighten to 0.375rem side padding at 0.875rem, and the readout drops under its flap row. At 30rem and below the two quota counters keep their short labels and drop the limits.

**The Own Frame Rule.** Wide content scrolls inside its own frame; the page never scrolls sideways at 375 px.

## Elevation & Depth

Flat paint. There are no drop shadows anywhere. Depth is mechanical and comes from three devices: the two-tone flap (upper leaf `flap`, lower leaf `flap-low`) with a hinge bar across the middle; a 2px inset hinge lip under the language key that disappears when it is pressed; and double inset rings on enamel plates (a cream gap ring, then an ink ring) that read as a printed border on stamped metal.

### Shadow Vocabulary
- **Key lip** (`box-shadow: inset 0 -2px 0 var(--hinge)`): bottom edge of the language key at rest; removed on press with a 1px downward shift.
- **Small plate ring** (`box-shadow: inset 0 0 0 3px var(--plate), inset 0 0 0 4px var(--plate-ink)`): the sources plate.
- **Large plate ring** (`box-shadow: inset 0 0 0 5px var(--plate), inset 0 0 0 7px var(--plate-ink)`): the legal plate and the source plate.
- **Ticker plate ring** (`box-shadow: inset 0 0 0 2px var(--plate), inset 0 0 0 3px var(--plate-ink)`): ticker plates in a plan step.
- **Push plate** (`box-shadow: inset 0 0 0 3px var(--plate), inset 0 0 0 4px var(--plate-ink), inset 0 -2px 0 var(--letter-muted)`): the small plate ring plus a faded-letter lip; on press the shadow goes and the plate drops 1px, like a key.
- **Blank flap field** (`background: flap upper half over flap-low; box-shadow: inset 0 -2px 0 var(--hinge)`): a field is a blank two-leaf flap with a key lip.

### Named Rules
**The Flat Paint Rule.** Every shadow is inset and describes a physical edge (a lip, a printed ring). No outer shadows, no blur, no glow, no gradients. The two-leaf field paint is a hard stop between two flat colors, not a gradient.

## Shapes

Hardware corners: small and fixed. Flap tiles round by a fraction of their row (2px to 5px), the key by 3px, the small plate by 6px and the large plate by 8px. Ticker plates share the small plate's 6px; blank-flap fields and the chart plot take the key's 3px. Larger fixtures get slightly larger radii, as stamped metal does; nothing is a pill or a soft card. Chart line markers are square (1px corner), circle, diamond and triangle; lines join round. Lines are 1px rules in `rule` color running the full content width. The hinge is the recurring silhouette: every tile is split in half horizontally.

## Components

### Buttons
Board keys: a flap face you press.
- **Shape:** squared key (3px), at least 2.75rem tall for touch.
- **Primary:** Flap Graphite face, Warm Letter White text in the button style, 0.5rem 1rem padding, Hinge Black bottom lip.
- **Hover / Focus:** face lightens to Pressed Flap on hover; focus is the global 2px Warm Letter White outline at 3px offset. On press the lip disappears and the key drops 1px. Board keys are uppercase with 1.25rem side padding; a disabled key fades its letters to Faded Letter.
- **Push plate (the one action to take):** a cream enamel plate that presses: Enamel Cream face, Plate Ink letters, the small plate ring and a Faded Letter lip. It is the only plate that moves, and there is one per decision band (approve, send the edit, or answer in simple mode). Focus keeps the letter outline.
- **Flap key (secondary):** the ordinary board key beside it (edit, discard, add step).
- **Quiet key (dismiss):** no face and no lip, underlined at 1px with 0.25em offset; a flap face appears on hover. Cancel is always the quiet key, last in the row.
- **Icon key:** a square 2.75rem board key holding a hand-drawn 14px SVG (up, down, remove) in `currentColor`, 2px stroke, square caps; disabled at 45% opacity. Every icon key carries a spoken name.

### Navigation
The top strip is the board's header: the sources plate on the left, the language key on the right, a 1px rule under them. The language key flips the whole board between Turkish and English, and every flap row re-spins to the new language.

### Split-Flap Row (signature)
A row of equal 3:4 tiles sized from the row's width. Each tile: Flap Graphite upper leaf, Lower Leaf bottom half, a Hinge Black bar across the middle (at least 2px), one condensed capital centered. Blank cells keep the tile and hide the letter. Upper-casing uses the content's locale, with the brand name always cased in English so "Disclosly" never gets a dotted capital I. When the text changes, each tile shows rotating characters (45ms frames) and settles left to right, three frames per tile; under `prefers-reduced-motion` the text sets at once. Plain tone uses Warm Letter White letters; live tone uses Enamel Amber.

### Enamel Plate (signature)
Cream face, Plate Ink text, double inset ring border. Small plate (6px, tight padding, label type at 700 uppercase) for short fixed signage like the sources. Large plate (8px, 1.25rem 1.5rem padding, title plus body) for the legal notice, anchored to the bottom of the board.

### Placard
The tagline band directly under the name row: headline type in Warm Letter White, framed above and below by 1px rules, 1rem vertical padding, no background.

### Quota Counters
Two counters in the top strip, left of the language key: questions and deep runs. Each is a label, a two-tile flap row right-aligned with a leading zero, and the daily limit (a slash and the number) in label type; the full sentence is screen-reader text.

### Plan Band
The decision a paused deep run waits on, set on the board under the stage row, not in a modal. Head: the row label ("PLAN") and a nine-tile live flap row with the decision word. Then the intro sentence with its expiry time, a meta list (estimate, deep runs left) in label-and-figure pairs, and the step rows. Each step: a single flap tile with the step number, the goal in caption size, then a tag line with the kind in Label Small, each ticker as a ticker plate, and the periods in Faded Letter joined by middle dots. The action row closes the band: push plate, flap key, quiet cancel.

### Inputs / Fields
- **Style:** a blank two-leaf flap to write on: Flap Graphite upper half over Lower Leaf, a 2px Hinge Black lip, 3px corners, at least 2.75rem tall, no border box. The question field is the long version (5px, larger type); plan fields are the small version, with Label Small labels above. Selects carry a 12px letter chevron drawn in SVG.
- **Focus:** the global letter outline.
- **Error:** a 1px letter ring inside the lip, and the reason directly under its own field behind a 1px letter rule (The No Alarm Rule).

### Report Table
A quote board in rows. Caption-size title, then the grid in its own scroll frame: Label Small heads between two rules, 0.625rem 0.75rem cell padding, a rule under every row, first column flush left and set at 600, numeric columns right-aligned in tabular numerals. A cited cell carries a small marker key (0.6875rem) that opens its source plate under the table.

### Report Chart
Hand-drawn SVG on the housing ground, no frame. Grid lines in Housing Rule, the zero line in Faded Letter, axis text 12px Faded Letter at width 80 with compact numbers. Series follow The Ink, Not Hue Rule. Above the plot sits the readout: an eight-tile plain flap row with the focused x value, which spins as focus moves, and each series' value with its legend key. Pointer or arrow keys move focus (the plot is a slider; Home and End jump to the ends); the focused column is shaded Flap Graphite, its x label turns Warm Letter White at 700, and its markers fill solid. Without focus the readout shows the last point.

### Confidence Row and Unverified List
The confidence row is a label, a six-tile plain flap row with the level word, and the writer's note in Faded Letter. What the checks could not confirm follows as a plain bulleted list in Faded Letter under its row label, capped at 60ch.

## Do's and Don'ts

### Do:
- **Do** set the product name and live values on split-flap rows, with the same text present as real text for assistive technology.
- **Do** reserve Enamel Amber for the letters of the live row.
- **Do** put fixed signage (sources, legal notice) on enamel plates with the inset ink ring.
- **Do** condense with Archivo's width axis (68 / 80 / 85).
- **Do** size flap tiles from their row's container width, so rows hold at 360 px.
- **Do** honor `prefers-reduced-motion` by setting flaps without the spin.
- **Do** give each decision exactly one push plate, a flap key for the alternative, and a quiet key to cancel.
- **Do** set figures in tabular numerals, right-aligned in tables, with label-style column heads.
- **Do** tell chart series apart by dash pattern, marker shape and the two letter inks.
- **Do** put a reason directly under the field it is about, behind a 1px letter rule.

### Don't:
- **Don't** put content on a centered card over a background, and don't use soft drop shadows or hairline form boxes; the board itself is the surface.
- **Don't** add glow, gradients or any outer shadow; depth is leaves, hinges and inset rings.
- **Don't** use amber for controls, links, borders or fills.
- **Don't** switch to a light palette for light-mode visitors.
- **Don't** put a small uppercase label above a headline as a kicker; the uppercase label style only names a flap row, a field, a column or a series.
- **Don't** put a decision in a modal or floating dialog; it is a band on the board under the row it pauses.
- **Don't** use cream enamel for a second action in the same group, or for anything that is not the one action to take or fixed signage.
- **Don't** add a color for a chart series or for an error.
- **Don't** let a wide table or chart push the page sideways.
