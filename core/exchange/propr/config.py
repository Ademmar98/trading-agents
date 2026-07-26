"""
Propr Challenge Config — Rules, risk parameters, and strategy settings.
"""
from dataclasses import dataclass, field
from enum import Enum


class ChallengeType(Enum):
    CLASSIC_1STEP = "classic_1step"
    TURBO_1STEP = "turbo_1step"
    CLASSIC_2STEP = "classic_2step"


class AccountSize(Enum):
    K5 = 5000
    K10 = 10000
    K25 = 25000
    K50 = 50000
    K100 = 100000


@dataclass
class ChallengeRules:
    challenge_type: ChallengeType
    account_size: AccountSize

    @property
    def starting_balance(self) -> float:
        return self.account_size.value

    @property
    def profit_target(self) -> float:
        targets = {
            (ChallengeType.CLASSIC_1STEP, AccountSize.K5): 500,
            (ChallengeType.CLASSIC_1STEP, AccountSize.K10): 1000,
            (ChallengeType.CLASSIC_1STEP, AccountSize.K25): 2500,
            (ChallengeType.CLASSIC_1STEP, AccountSize.K50): 5000,
            (ChallengeType.CLASSIC_1STEP, AccountSize.K100): 10000,
            (ChallengeType.TURBO_1STEP, AccountSize.K5): 450,
            (ChallengeType.TURBO_1STEP, AccountSize.K10): 900,
            (ChallengeType.TURBO_1STEP, AccountSize.K25): 2250,
            (ChallengeType.TURBO_1STEP, AccountSize.K50): 4500,
            (ChallengeType.TURBO_1STEP, AccountSize.K100): 9000,
        }
        return targets.get((self.challenge_type, self.account_size), 0)

    @property
    def profit_target_pct(self) -> float:
        return self.profit_target / self.starting_balance

    @property
    def max_daily_loss_pct(self) -> float:
        if self.challenge_type == ChallengeType.CLASSIC_2STEP:
            return 0.05
        return 0.03

    @property
    def max_daily_loss_usd(self) -> float:
        return self.starting_balance * self.max_daily_loss_pct

    @property
    def max_drawdown_pct(self) -> float:
        if self.challenge_type == ChallengeType.TURBO_1STEP:
            return 0.03
        if self.challenge_type == ChallengeType.CLASSIC_2STEP:
            return 0.08
        return 0.06

    @property
    def max_drawdown_usd(self) -> float:
        return self.starting_balance * self.max_drawdown_pct

    @property
    def max_drawdown_equity_floor(self) -> float:
        return self.starting_balance * (1 - self.max_drawdown_pct)

    @property
    def max_leverage(self) -> dict[str, int]:
        return {
            "BTC": 5, "ETH": 5,
            "default_crypto": 2,
            "equities": 4,
            "sp500_xyz100": 10,
            "indices": 5,
            "commodities": 5,
            "fx": 25,
        }


@dataclass
class ProprConfig:
    # Challenge settings
    challenge_type: ChallengeType = ChallengeType.CLASSIC_1STEP
    account_size: AccountSize = AccountSize.K5

    # API
    api_key: str = ""
    api_url: str = "https://api.propr.xyz/v1"
    ws_url: str = "wss://api.propr.xyz/ws"

    # Strategy — Microstructure Absorption (validated M3)
    m3_enabled: bool = True
    m3_cvd_thresh: float = -0.05   # CVD below this = selling pressure
    m3_rv_thresh: float = 0.03     # RV above this = enough volatility
    m3_dip_pct: float = 0.008      # At least 0.8% from recent high
    m3_sl_multiplier: float = 1.5
    m3_tp_multiplier: float = 3.0

    # Strategy — Z-Score Funding (M1)
    m1_enabled: bool = False

    # Strategy — Basket Rebalancing (M2)
    m2_enabled: bool = False

    # Risk management
    kelly_fraction: float = 1.0
    max_position_pct: float = 0.60
    max_concurrent_positions: int = 5
    min_trade_interval_sec: int = 60

    # Ceiling on the SUM of open stop distances, as a fraction of the MAX
    # DRAWDOWN allowance -- not the daily one. Position count alone does not
    # bound loss: correlated crypto longs stop out together, so N positions at
    # 2% each is an N*2% loss.
    #
    # Measured against max drawdown because that is what actually ends a
    # challenge, and positions here are held across days (36h max), so their
    # stops are not confined to one session. At 1.0 the budget is the full
    # $300 wall, which admits exactly the walk-forward-validated dip config
    # (3 concurrent x 2% of equity). That configuration ran 6 non-overlapping
    # 30-day windows with 0 failures and a worst realised drawdown of 4.06% --
    # the theoretical 6% never materialised, because stops do not all fire
    # together and the trailing stop cuts losers early.
    #
    # The cap still binds on anything larger: a 60%-position strategy risks
    # $300 in ONE ticket and is refused after the first.
    max_dd_risk_fraction: float = 1.0

    # Circuit breakers (adapted for prop firm rules)
    max_daily_loss_pct: float = 0.025  # Stay under 3% daily limit
    max_monthly_dd_pct: float = 0.05   # Stay under 6% max DD
    circuit_breaker_cooldown_sec: int = 3600

    # Trading
    quote_asset: str = "USDC"
    fee_rate: float = 0.00075  # 0.075% taker/maker

    # Symbols to trade (Hyperliquid perps)
    symbols: list[str] = field(default_factory=lambda: [
        "BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "LINK",
        "SUI", "NEAR", "AAVE", "INJ", "FET",
    ])

    # Hyperliquid szDecimals per asset
    symbol_decimals: dict[str, int] = field(default_factory=lambda: {
        "BTC": 5, "ETH": 4, "SOL": 2, "DOGE": 0, "XRP": 0,
        "AVAX": 2, "LINK": 1, "SUI": 1, "NEAR": 2, "AAVE": 2,
        "INJ": 1, "FET": 1, "ATOM": 2, "MATIC": 1, "DYDX": 1,
        "OP": 1, "ARB": 1, "APT": 2, "CRV": 1, "LDO": 1,
        "RNDR": 1, "STX": 1, "BCH": 3, "COMP": 2,
    })

    @property
    def rules(self) -> ChallengeRules:
        return ChallengeRules(self.challenge_type, self.account_size)

    def max_leverage_for(self, asset: str) -> int:
        rules = self.rules
        overrides = rules.max_leverage
        return overrides.get(asset, overrides["default_crypto"])

    def position_size_usd(self, equity: float, kelly: float | None = None) -> float:
        kf = kelly or self.kelly_fraction
        return equity * self.max_position_pct * kf
