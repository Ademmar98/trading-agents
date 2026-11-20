"""
Configuration for the Hyperliquid three-module trading system.

Everything tunable lives here. Nothing below this file reads os.environ, so a
test can build a Config by hand and never touch the environment.

HALAL CONSTRAINT (repo invariant, see CLAUDE memory)
----------------------------------------------------
`long_only` and `spot_mode` default True. Under that pair of defaults the
system cannot short, cannot borrow, and never holds a funding-bearing perp
position. Module B's "short when Z > +2.5" leg degrades to "exit to flat",
which is the spec's own stated spot behaviour ("short/spot exits").

Flipping `spot_mode=False` enables 1x perps. That is a deliberate act: 1x perp
is still leverage-capable margin and it pays/receives funding. Left available
because the brief allows "fully collateralized perps at 1x", but OFF by
default because this repo's standing constraint forbids it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- API ------


@dataclass(frozen=True)
class ApiConfig:
    """Credentials and endpoint. Never log `secret_key`."""

    secret_key: str                      # API-wallet private key (0x...)
    account_address: str                 # the MASTER account address the wallet trades for
    base_url: str = "https://api.hyperliquid.xyz"
    testnet: bool = False
    #: seconds; the SDK passes this straight to requests
    timeout: float = 15.0

    @staticmethod
    def from_env() -> "ApiConfig":
        """
        Build from HL_SECRET_KEY / HL_ACCOUNT_ADDRESS / HL_TESTNET.

        An API wallet ("agent") is strongly preferred over the master key: it
        can trade but cannot withdraw. Generate one at app.hyperliquid.xyz.
        """
        sk = os.environ.get("HL_SECRET_KEY", "").strip()
        addr = os.environ.get("HL_ACCOUNT_ADDRESS", "").strip()
        if not sk or not addr:
            raise RuntimeError(
                "HL_SECRET_KEY and HL_ACCOUNT_ADDRESS must be set. "
                "Use an API wallet (agent) key, not your master key."
            )
        testnet = os.environ.get("HL_TESTNET", "0") == "1"
        return ApiConfig(
            secret_key=sk,
            account_address=addr,
            base_url=("https://api.hyperliquid-testnet.xyz" if testnet
                      else "https://api.hyperliquid.xyz"),
            testnet=testnet,
        )


# --------------------------------------------------------------- RISK ------


@dataclass(frozen=True)
class RiskConfig:
    """
    Drawdown tiers are measured peak-to-trough on ACCOUNT VALUE, where peak is
    the high-water mark persisted across restarts. A prior study in this repo
    (#16) shipped a max_dd that was pure artifact because the equity curve
    added open-position entry cost on top of mark-to-market. Here equity comes
    straight from the exchange's own accountValue, which cannot double-count.
    """

    # --- tiered peak-to-trough safeguards (percent of high-water mark) ---
    dd_reduce_pct: float = 4.0        # halve all sizing
    dd_liquidate_pct: float = 4.8     # cancel all, passively flatten to USDC
    dd_hard_lock_pct: float = 5.0     # permanent halt + alert
    size_reduction_factor: float = 0.5

    # --- rolling daily loss freeze ---
    daily_loss_limit_pct: float = 1.5   # 24h account PnL below -1.5% ...
    freeze_hours: float = 12.0          # ... freezes NEW entries this long

    # --- volatility-parity sizing ---
    risk_factor: float = 0.004          # 0.4% of capital risked per position
    atr_multiplier: float = 2.0         # stop distance in ATR units
    atr_period: int = 14                # ATR lookback (in bars of atr_interval)
    atr_interval: str = "1d"            # "14-day ATR" per the brief

    # --- hard exposure caps (fraction of account value) ---
    max_exposure_major: float = 0.15    # BTC / ETH
    max_exposure_alt: float = 0.03      # everything else
    majors: frozenset[str] = frozenset({"BTC", "ETH"})

    # --- portfolio-level ---
    max_concurrent_positions: int = 8
    max_gross_exposure: float = 1.0     # 1.0 == zero leverage. Do not raise.

    def __post_init__(self) -> None:
        if self.max_gross_exposure > 1.0:
            raise ValueError("max_gross_exposure > 1.0 is leverage; forbidden")
        if not (self.dd_reduce_pct < self.dd_liquidate_pct < self.dd_hard_lock_pct):
            raise ValueError("drawdown tiers must be strictly increasing")


# ------------------------------------------------------------ SIGNALS ------


@dataclass(frozen=True)
class SignalConfig:
    """Per-module parameters. Interval strings are Hyperliquid candle intervals."""

    # --- Module A: microstructure / CVD (15m) ---
    a_interval: str = "15m"
    a_cvd_lookback: int = 96            # bars of CVD history kept (24h at 15m)
    a_divergence_lookback: int = 12     # bars used to test price vs CVD slope
    a_wall_depth_levels: int = 10       # L2 levels aggregated per side
    a_wall_ratio: float = 2.0           # bid depth >= 2x ask depth == bid wall
    a_min_cvd_z: float = 1.0            # |z| of CVD slope needed to call divergence

    # --- Module B: funding rate Z-score (1h) ---
    b_interval: str = "1h"
    b_zscore_window: int = 72           # rolling 72 x 1h funding samples
    b_entry_z: float = -2.5             # long when Z < -2.5
    b_exit_z: float = 2.5               # exit  when Z > +2.5
    b_neutral_z: float = 0.0            # revert to neutral crossing 0
    b_min_samples: int = 72             # never score on a partial window

    # --- Module C: multi-anchor VWAP mean reversion (1h / 4h) ---
    c_interval: str = "1h"
    c_confirm_interval: str = "4h"
    c_session_hours: int = 24           # rolling session VWAP
    c_anchor_days: int = 7              # anchored weekly VWAP
    c_band_sigma: float = 2.0           # +/- 2 sigma bands
    c_volume_expansion: float = 1.5     # vs 20-period volume SMA
    c_volume_sma: int = 20
    #: veto a -2sigma entry if 4h price is this far below its 6-bar high
    c_trend_veto_pct: float = 0.15

    # --- combination ---
    #: a symbol trades only if at least this many modules agree on direction
    min_module_agreement: int = 1
    #: modules are weighted equally; a 2-module agreement doubles conviction
    conviction_scales_size: bool = True


# ---------------------------------------------------------- EXECUTION ------


@dataclass(frozen=True)
class ExecConfig:
    """
    100% passive. Every order is {"limit": {"tif": "Alo"}} — Hyperliquid's
    Add-Liquidity-Only flag. An ALO order that would cross the book is
    REJECTED by the exchange rather than filled as taker, which is exactly the
    guarantee we want: it is impossible for this system to pay taker fees.
    """

    post_only: bool = True
    #: quote this many ticks inside our side of the book (0 == join best)
    passive_offset_ticks: int = 0
    #: cancel+re-quote an unfilled order after this many candles of a_interval
    unfilled_timeout_candles: int = 3
    #: re-quote at most this many times before abandoning the signal
    max_requotes: int = 5
    #: don't bother with orders below this notional (exchange min is $10)
    min_order_notional_usd: float = 12.0
    #: leverage set on every perp asset at startup. MUST stay 1.
    leverage: int = 1
    #: poll interval for the main loop
    loop_interval_sec: float = 5.0
    #: how many symbols to stream L2/trades for (WS subscription budget)
    max_streamed_symbols: int = 40


# ------------------------------------------------------------- SYSTEM ------


@dataclass(frozen=True)
class SystemConfig:
    api: ApiConfig
    risk: RiskConfig = field(default_factory=RiskConfig)
    signals: SignalConfig = field(default_factory=SignalConfig)
    execution: ExecConfig = field(default_factory=ExecConfig)

    # --- universe ---
    #: None == all discovered assets. Otherwise an explicit allow-list.
    universe: tuple[str, ...] | None = None
    #: skip assets whose 24h notional volume is below this
    min_daily_volume_usd: float = 5_000_000.0

    # --- halal invariants (see module docstring) ---
    long_only: bool = True
    spot_mode: bool = True

    # --- ops ---
    state_path: Path = Path(__file__).parent / "state.json"
    log_path: Path = Path(__file__).parent / "hl_system.log"
    dry_run: bool = True          # nothing is sent to the exchange unless False

    def __post_init__(self) -> None:
        if self.execution.leverage != 1:
            raise ValueError("leverage must be 1 (zero-leverage mandate)")
        if not self.spot_mode and self.long_only:
            # allowed, but the operator should know perps accrue funding
            pass

    @staticmethod
    def default(dry_run: bool = True) -> "SystemConfig":
        return SystemConfig(api=ApiConfig.from_env(), dry_run=dry_run)
