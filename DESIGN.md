# Design System: Trading Agent Firm Dashboard

Style: **Editorial Brutalism** (Bloomberg Businessweek lineage), per the
huashu-design style library. Previous rounded-dark-card design was retired —
it sat squarely in that library's "banned clichés" (uniform dark-slate base,
accent glow, rounded cards).

## 1. Visual Theme & Atmosphere
A newsroom trading terminal. Pure black ground, white ink, structure carried
entirely by rule lines — no cards, no shadows, no rounded corners, no glow.
Numbers are the imagery: the account equity is set as a giant headline. Dense,
squared-off, unapologetically utilitarian. Every event carries an exact
HH:MM:SS timestamp, like a tape.

## 2. Color Palette & Roles
Derivation: black ground and white ink from terminal heritage; green/red are
the tape's own profit/loss semantics, brightened for legibility on black.
- **Terminal Black (#000000)** — the page. Not near-black: black.
- **Ink White (#FFFFFF)** — text, strong rules (masthead 4px, table heads 1px).
- **Hairline Gray (#2A2A2A)** — quiet rules dividing cells, rows, columns.
- **Tape Green (#00C24E)** — profit, buy side, LIVE dot, rising equity line.
- **Signal Red (#FF433D)** — loss, sell side, offline state, falling line.
- **Gray Ink (#9A9AA2)** — labels, axis ticks, secondary text.

## 3. Typography Rules
Modular scale 1.2 (dashboard density), body 15px Inter.
- **Archivo Black/Bold** — masthead and section titles only. Display duty.
- **Inter 400–600** — small body workhorse (its correct role; never display).
- **JetBrains Mono** — every number, timestamp, label, and badge;
  `font-variant-numeric: tabular-nums` throughout. Uppercase, letter-spaced
  labels (0.08–0.1em).

## 4. Component Stylings
- **Stat strip:** one grid row of cells split by 1px hairlines. No boxes.
- **Tables:** header row under a 1px white rule; body rows under hairlines;
  numerals right-aligned mono; first column is always the exact time.
- **Activity log:** `HH:MM:SS` white mono, agent name gray uppercase, message
  in Inter. Reads like a wire feed.
- **Chart:** single 2px line, green when up / red when down, no fill, faint
  #1A1A1A grid, mono ticks, square white tooltip.
- **Status:** square (not round) green dot, step-blink; red when offline.

## 5. Layout Principles
Max width 1280px. Masthead (4px rule) → stat strip → full-width chart →
positions table → two hairline-divided columns (trade history | activity).
Collapses 5→3→2 stat columns and to a single column below 900px.
Sections carry mono index marks (01–04).
