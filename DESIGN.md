# Design System: Trading Agent Firm Dashboard

## 1. Visual Theme & Atmosphere
A dense, utilitarian night-trading terminal with a calm, OLED-dark atmosphere.
The mood is "mission control at 2 AM": deep midnight surfaces, restrained amber
signal lighting, and data as the hero. No decoration that does not carry
information. High contrast for glanceability, whisper-soft depth, minimal glow
on live elements only.

## 2. Color Palette & Roles
- **Abyss Navy (#0A0F1E)** — page background; the darkest layer.
- **Midnight Slate (#0F172A)** — card/panel surfaces, one step above the abyss.
- **Hairline Steel (#1E293B)** — borders and dividers; visible but quiet.
- **Signal Amber (#F59E0B)** — primary accent: headings, equity line, live dot.
- **Soft Gold (#FBBF24)** — hover states of amber elements.
- **Electric Violet (#8B5CF6)** — secondary accent for agent activity.
- **Profit Emerald (#10B981)** — positive P&L, wins, buy side.
- **Loss Crimson (#EF4444)** — negative P&L, losses, sell side.
- **Frost White (#F8FAFC)** — primary text.
- **Muted Slate (#94A3B8)** — secondary text, labels, timestamps.

## 3. Typography Rules
- **Headings & numerals:** Orbitron — geometric, technical, used sparingly for
  the brand mark and the big money figures.
- **Body & tables:** Exo 2 — legible at small sizes, slightly futuristic,
  weights 300–600. Labels are uppercase, letter-spaced (0.08em), muted slate.

## 4. Component Stylings
- **Stat cards:** subtly rounded corners (10px), midnight slate surface,
  hairline steel border, no shadow — depth comes from layered darkness.
- **Tables:** borderless rows separated by hairline dividers; numeric columns
  right-aligned; P&L cells colored emerald/crimson.
- **Badges:** pill-shaped, tinted backgrounds at 15% opacity of their accent.
- **Live indicator:** small amber dot with a soft pulsing glow
  (respects prefers-reduced-motion).

## 5. Layout Principles
- Single-page dashboard, max width 1200px, centered.
- Responsive grid: 6 stat cards → 3 → 2 columns as the viewport narrows.
- Chart is full-width below the stats; positions, history, and activity stack
  beneath it. Consistent 16px gutter rhythm throughout.
