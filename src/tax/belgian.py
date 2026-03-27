"""
Belgian Tax Calculator for IBKR investors.
Covers: TOB, Capital Gains Tax (2026+), Dividend Withholding, Reynders Tax.

IMPORTANT: This is informational only. Consult a Belgian tax advisor.
Foreign brokers like IBKR do NOT withhold Belgian taxes automatically.
You must self-declare via MyMinfin > Diverse taksen (Divtax).
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class InstrumentType(Enum):
    STOCK = "stock"
    ETF_EQUITY = "etf_equity"
    ETF_BOND_HEAVY = "etf_bond_heavy"  # >10% bonds → Reynders Tax
    BOND = "bond"


@dataclass
class TOBCalculation:
    """Tax on Stock Exchange Transactions (Taks op Beursverrichtingen)."""
    instrument_type: InstrumentType
    transaction_value_eur: float
    tob_rate: float
    tob_amount: float
    capped: bool = False  # TOB has per-transaction caps


@dataclass
class CGTCalculation:
    """Capital Gains Tax (10%, effective Jan 2026)."""
    ticker: str
    purchase_price: float
    sale_price: float
    baseline_price_dec2025: float
    gain: float
    taxable_gain: float
    tax_amount: float
    used_original_cost: bool  # True if original cost > Dec 2025 baseline


@dataclass
class TaxSummary:
    period: str
    total_tob: float
    total_cgt: float
    total_dividend_tax: float
    total_reynders_tax: float
    grand_total: float
    annual_cgt_exemption_remaining: float
    notes: list[str]


class BelgianTaxCalculator:
    """
    Calculator for Belgian investment taxes as of 2026.

    Key rules:
    - TOB: 0.35% stocks, 0.12% standard ETFs, 1.32% bond-heavy ETFs
    - CGT: 10% on realized gains, EUR 10,000 annual exemption
    - Dividends: 30% withholding
    - Reynders: 30% on gains from ETFs with >10% bond allocation
    - IBKR does NOT withhold — you self-declare
    """

    # TOB rates
    TOB_RATES = {
        InstrumentType.STOCK: 0.0035,
        InstrumentType.ETF_EQUITY: 0.0012,
        InstrumentType.ETF_BOND_HEAVY: 0.0132,
        InstrumentType.BOND: 0.0012,
    }

    # TOB caps per transaction
    TOB_CAPS = {
        InstrumentType.STOCK: 1600.0,
        InstrumentType.ETF_EQUITY: 1600.0,
        InstrumentType.ETF_BOND_HEAVY: 4000.0,
        InstrumentType.BOND: 1600.0,
    }

    CGT_RATE = 0.10
    CGT_ANNUAL_EXEMPTION = 10_000.0
    DIVIDEND_TAX_RATE = 0.30
    REYNDERS_TAX_RATE = 0.30

    def calculate_tob(
        self, transaction_value_eur: float, instrument_type: InstrumentType
    ) -> TOBCalculation:
        """Calculate TOB for a single transaction."""
        rate = self.TOB_RATES[instrument_type]
        cap = self.TOB_CAPS[instrument_type]
        raw_tob = transaction_value_eur * rate
        capped = raw_tob > cap
        tob = min(raw_tob, cap)

        return TOBCalculation(
            instrument_type=instrument_type,
            transaction_value_eur=transaction_value_eur,
            tob_rate=rate,
            tob_amount=round(tob, 2),
            capped=capped,
        )

    def calculate_cgt(
        self,
        ticker: str,
        purchase_price: float,
        sale_price: float,
        baseline_dec2025: float,
        remaining_exemption: float = 10_000.0,
    ) -> CGTCalculation:
        """
        Calculate capital gains tax on a sale.

        Until Dec 31, 2030: use max(purchase_price, baseline_dec2025) as cost basis.
        After 2030: must use baseline_dec2025.
        """
        today = date.today()
        transition_end = date(2030, 12, 31)

        if today <= transition_end:
            # During transition: use whichever is higher (more favorable)
            cost_basis = max(purchase_price, baseline_dec2025)
            used_original = purchase_price > baseline_dec2025
        else:
            cost_basis = baseline_dec2025
            used_original = False

        gain = sale_price - cost_basis
        taxable = max(0, gain - remaining_exemption) if gain > 0 else 0
        tax = taxable * self.CGT_RATE

        return CGTCalculation(
            ticker=ticker,
            purchase_price=purchase_price,
            sale_price=sale_price,
            baseline_price_dec2025=baseline_dec2025,
            gain=round(gain, 2),
            taxable_gain=round(taxable, 2),
            tax_amount=round(tax, 2),
            used_original_cost=used_original,
        )

    def calculate_dividend_tax(self, gross_dividend_eur: float) -> float:
        """30% withholding on dividends."""
        return round(gross_dividend_eur * self.DIVIDEND_TAX_RATE, 2)

    def estimate_trade_cost(
        self,
        value_eur: float,
        instrument_type: InstrumentType = InstrumentType.STOCK,
        ibkr_commission_eur: float = 1.0,
    ) -> dict:
        """
        Estimate total cost of a trade including:
        - IBKR commission
        - TOB (Belgian transaction tax)
        - Spread cost estimate
        """
        tob = self.calculate_tob(value_eur, instrument_type)
        spread_estimate = value_eur * 0.0005  # ~0.05% typical spread

        total_cost = ibkr_commission_eur + tob.tob_amount + spread_estimate
        cost_pct = total_cost / value_eur * 100

        return {
            "trade_value_eur": value_eur,
            "ibkr_commission": ibkr_commission_eur,
            "tob": tob.tob_amount,
            "tob_rate": f"{tob.tob_rate:.2%}",
            "spread_estimate": round(spread_estimate, 2),
            "total_cost": round(total_cost, 2),
            "total_cost_pct": f"{cost_pct:.2f}%",
            "note": "IBKR does not withhold TOB — self-declare via MyMinfin/DivTax",
        }
