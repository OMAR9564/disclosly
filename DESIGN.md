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
  placard:
    textColor: "{colors.letter}"
    typography: "{typography.headline}"
    padding: "1rem 0"
---

# Design System: Disclosly

## Overview

**Creative North Star: "The Trading-Hall Board"**

The whole viewport is the housing of a departures-style quote board in a dim trading hall. Words that matter are set on split-flap tiles: graphite leaves a step lighter than the housing, each cut across the middle by a black hinge, carrying warm off-white condensed capitals. Fixed information that a real board would carry as printed signage (the sources, the legal notice) sits on cream enamel plates with an inked border ring. Everything else is plain lettering on the housing, divided by thin rules.

The board is a physical object, so it is flat paint only: no glow, no gradient, no soft drop shadow, no floating card. Depth comes from the two-tone flap leaves, the hinge line and inset enamel rings, never from light. It keeps one dark palette regardless of the visitor's color scheme. The one moving part is the flap: when a value changes, the tiles spin through characters and settle left to right; under reduced motion they set at once.

Density is low and the scale is large. The name row spans the full content width, and each flap row sizes its tiles from its own width, so the board reads the same at 360 px and at 1440 px.

**Key Characteristics:**
- Matte graphite housing, flap tiles a step lighter, a hinge across every tile.
- Warm off-white condensed caps (Archivo, width axis 68 to 85).
- One enamel amber, reserved for the live row.
- Cream enamel plates with inset ink rings for fixed signage.
- Flat paint; depth is built from leaves, hinges and inset rings.
- Flaps spin and settle left to right on change; reduced motion sets them at once.

## Colors

A near-monochrome warm graphite board with one enamel amber and cream enamel plates.

### Primary
- **Enamel Amber** (`amber`): the letter color of the live flap row only, today the "available" state word. It marks the one value on the board that is currently true and working.

### Neutral
- **Matte Graphite Housing** (`housing`): page background, the board itself, and the browser theme color.
- **Flap Graphite** (`flap`): upper leaf of every flap tile and the face of the language key.
- **Pressed Flap** (`flap-hover`): the key's face on hover.
- **Lower Leaf** (`flap-low`): the lower half of each tile, one step darker than the upper leaf.
- **Hinge Black** (`hinge`): the hinge line across each tile and the bottom lip of the key.
- **Warm Letter White** (`letter`): flap letters, body text, focus ring and selection fill.
- **Faded Letter** (`letter-muted`): row labels and the plain sentence under a flap row.
- **Enamel Cream** (`plate`): the face of the enamel plates. Same value as Warm Letter White, but a separate role: plate surface, not ink.
- **Plate Ink** (`plate-ink`): text on plates and the plates' inner border ring.
- **Housing Rule** (`rule`): 1px dividers under the top strip and around the tagline placard.

### Named Rules
**The One Amber Rule.** Amber colors the letters of the live row and nothing else: no amber buttons, links, borders or backgrounds. If two things are amber, one is wrong.

**The One Board Rule.** The palette does not invert for light mode (`color-scheme: dark`). A board in a hall does not change paint with the visitor's settings.

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
- **Button** (700, width 80, 0.9375rem, 0.04em): the language key.

### Named Rules
**The Width Axis Rule.** Condensing is done with Archivo's `wdth` axis (68 flaps, 80 labels and keys, 85 plate titles), never with a second condensed family or with negative tracking.

**The Same-Text Rule.** Flap rows are decorative (`aria-hidden`); the same value always exists as real text nearby (a screen-reader name or the plain sentence under the row).

## Layout

The board is a single centered column (max 76rem) filling at least the full dynamic viewport height, with fluid padding (`board-pad`) and fluid gaps between its bands (`board-gap`). The bands stack in fixed order: top strip (sources plate left, language key right, rule beneath), name block, flap rows, and the legal plate pushed to the bottom edge by a flexible last track.

Flap rows are grids of equal tiles (`repeat(n, 1fr)`, 3:4 aspect, `tile-gap`) inside an inline-size container, so tile size, gap, radius, hinge and letter size all scale with the row's width. The status row places a 12-tile state row and a 7-tile version row side by side at 12fr / 7fr (max 52rem); at 30rem and below they stack and the version row shrinks to 58% width. Readable text is capped at 60ch on the board and 72ch on plates.

## Elevation & Depth

Flat paint. There are no drop shadows anywhere. Depth is mechanical and comes from three devices: the two-tone flap (upper leaf `flap`, lower leaf `flap-low`) with a hinge bar across the middle; a 2px inset hinge lip under the language key that disappears when it is pressed; and double inset rings on enamel plates (a cream gap ring, then an ink ring) that read as a printed border on stamped metal.

### Shadow Vocabulary
- **Key lip** (`box-shadow: inset 0 -2px 0 var(--hinge)`): bottom edge of the language key at rest; removed on press with a 1px downward shift.
- **Small plate ring** (`box-shadow: inset 0 0 0 3px var(--plate), inset 0 0 0 4px var(--plate-ink)`): the sources plate.
- **Large plate ring** (`box-shadow: inset 0 0 0 5px var(--plate), inset 0 0 0 7px var(--plate-ink)`): the legal plate.

### Named Rules
**The Flat Paint Rule.** Every shadow is inset and describes a physical edge (a lip, a printed ring). No outer shadows, no blur, no glow, no gradients.

## Shapes

Hardware corners: small and fixed. Flap tiles round by a fraction of their row (2px to 5px), the key by 3px, the small plate by 6px and the large plate by 8px. Larger fixtures get slightly larger radii, as stamped metal does; nothing is a pill or a soft card. Lines are 1px rules in `rule` color running the full content width. The hinge is the recurring silhouette: every tile is split in half horizontally.

## Components

### Buttons
Board keys: a flap face you press.
- **Shape:** squared key (3px), at least 2.75rem tall for touch.
- **Primary:** Flap Graphite face, Warm Letter White text in the button style, 0.5rem 1rem padding, Hinge Black bottom lip.
- **Hover / Focus:** face lightens to Pressed Flap on hover; focus is the global 2px Warm Letter White outline at 3px offset. On press the lip disappears and the key drops 1px.

### Navigation
The top strip is the board's header: the sources plate on the left, the language key on the right, a 1px rule under them. The language key flips the whole board between Turkish and English, and every flap row re-spins to the new language.

### Split-Flap Row (signature)
A row of equal 3:4 tiles sized from the row's width. Each tile: Flap Graphite upper leaf, Lower Leaf bottom half, a Hinge Black bar across the middle (at least 2px), one condensed capital centered. Blank cells keep the tile and hide the letter. Upper-casing uses the content's locale, with the brand name always cased in English so "Disclosly" never gets a dotted capital I. When the text changes, each tile shows rotating characters (45ms frames) and settles left to right, three frames per tile; under `prefers-reduced-motion` the text sets at once. Plain tone uses Warm Letter White letters; live tone uses Enamel Amber.

### Enamel Plate (signature)
Cream face, Plate Ink text, double inset ring border. Small plate (6px, tight padding, label type at 700 uppercase) for short fixed signage like the sources. Large plate (8px, 1.25rem 1.5rem padding, title plus body) for the legal notice, anchored to the bottom of the board.

### Placard
The tagline band directly under the name row: headline type in Warm Letter White, framed above and below by 1px rules, 1rem vertical padding, no background.

## Do's and Don'ts

### Do:
- **Do** set the product name and live values on split-flap rows, with the same text present as real text for assistive technology.
- **Do** reserve Enamel Amber for the letters of the live row.
- **Do** put fixed signage (sources, legal notice) on enamel plates with the inset ink ring.
- **Do** condense with Archivo's width axis (68 / 80 / 85).
- **Do** size flap tiles from their row's container width, so rows hold at 360 px.
- **Do** honor `prefers-reduced-motion` by setting flaps without the spin.

### Don't:
- **Don't** put content on a centered card over a background, and don't use soft drop shadows or hairline form boxes; the board itself is the surface.
- **Don't** add glow, gradients or any outer shadow; depth is leaves, hinges and inset rings.
- **Don't** use amber for controls, links, borders or fills.
- **Don't** switch to a light palette for light-mode visitors.
- **Don't** put a small uppercase label above a headline as a kicker; the uppercase label style only names a flap row.
