"""Per-family strategy packages. Each module exposes a FAMILY_STRATEGIES
registry of (display_name, fn) tuples mirroring core.strategies.ALL_STRATEGIES,
and every returned signal dict carries its own machine `strategy` tag so
per-strategy stats stay attributable (same pattern as core/swing.py)."""
