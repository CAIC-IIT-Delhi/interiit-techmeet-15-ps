"""Demo exchange: replays a price history CSV and settles with a settlement rules CSV.

Participants normally start it through demo/run_demo.py. It is an ordinary exchange config, so this
also works from the kit folder:

    python -m rfq.exchange --config demo.demo_exchange --rounds 12

Prices follow the CSV row by row, one row per price update (10 per 5-second interval), and hold at
the last row if the file runs out. Products come from the CSV's columns, so any product set can be
practised. The settings below are DEMO settings: each competition round publishes its own product
specifications, and real customers and opponents behave differently.

Environment variables (set by run_demo.py):
    RFQ_DEMO_DATA, RFQ_DEMO_RULES   the CSV files to replay
    RFQ_DEMO_START                  first data row to replay (0 = the first row)
    RFQ_DEMO_SEED                   seed for the demo customers (RFQs) and tie-breaks
    RFQ_DEMO_PRODUCTS               JSON overrides per product, e.g. {"C": {"max_long": 10}}
    RFQ_DEMO_RFQS                   customer RFQs per interval (default 3)
"""
import json
import os
from pathlib import Path

from rfq.models import HostConfig, Product, RandomCustomers, ReplayPrices

HERE = Path(__file__).resolve().parent
DATA = os.environ.get("RFQ_DEMO_DATA", str(HERE / "demo_data.csv"))
RULES = os.environ.get("RFQ_DEMO_RULES", str(HERE / "demo_settlement_rules.csv"))

# Demo specifications for the demo products; any other product gets DEFAULT_SPEC.
PRODUCT_SPECS = {
    "A": {"tick_size": 0.01, "lot_size": 1, "max_long": 50, "max_short": 50,
          "breach_penalty_per_unit": 1.0, "fee_per_unit": 0.01},
    "B": {"tick_size": 0.01, "lot_size": 1, "max_long": 25, "max_short": 25,
          "breach_penalty_per_unit": 2.0, "fee_per_unit": 0.02},
}
DEFAULT_SPEC = {"tick_size": 0.01, "lot_size": 1, "max_long": 50, "max_short": 50,
                "breach_penalty_per_unit": 1.0, "fee_per_unit": 0.01}
SPEC_FIELDS = {"tick_size", "lot_size", "multiplier", "tradable_against_exchange", "max_long", "max_short",
               "breach_penalty_per_unit", "fee_per_unit", "fee_rate"}
POSITIVE = {"tick_size", "lot_size", "multiplier"}
NONNEGATIVE_OR_NONE = {"max_long", "max_short", "breach_penalty_per_unit", "fee_per_unit", "fee_rate"}


def check_spec(product_id, spec):
    """Readable errors for --products settings, before anything runs."""
    bad = set(spec) - SPEC_FIELDS
    if bad:
        raise ValueError(f"{product_id}: unknown product settings {sorted(bad)}; allowed: {sorted(SPEC_FIELDS)}")
    for field, value in spec.items():
        if field == "tradable_against_exchange":
            if not isinstance(value, bool):
                raise ValueError(f"{product_id}: tradable_against_exchange must be true or false, got {value!r}")
            continue
        number = isinstance(value, (int, float)) and not isinstance(value, bool)
        if field in POSITIVE and not (number and value > 0):
            raise ValueError(f"{product_id}: {field} must be a positive number, got {value!r}")
        if field in NONNEGATIVE_OR_NONE and value is not None and not (number and value >= 0):
            raise ValueError(f"{product_id}: {field} must be a number >= 0 (or null), got {value!r}")


def build():
    start = int(os.environ.get("RFQ_DEMO_START", "0"))
    seed = int(os.environ.get("RFQ_DEMO_SEED", "42"))
    rfqs = int(os.environ.get("RFQ_DEMO_RFQS", "3"))
    if rfqs < 0:
        raise ValueError("the number of RFQs per interval must be 0 or more")
    overrides = json.loads(os.environ.get("RFQ_DEMO_PRODUCTS", "{}"))
    if not isinstance(overrides, dict) or not all(isinstance(v, dict) for v in overrides.values()):
        raise ValueError('product settings must look like {"C": {"max_long": 10}}')
    prices = ReplayPrices.from_csv(DATA, RULES, start_row=start)
    first = prices.rows[0]
    unknown = set(overrides) - set(first)
    if unknown:
        raise ValueError(f"product overrides for products not in {Path(DATA).name}: {sorted(unknown)}")
    products = []
    for product_id, initial in first.items():
        spec = {**PRODUCT_SPECS.get(product_id, DEFAULT_SPEC), **overrides.get(product_id, {})}
        check_spec(product_id, spec)
        products.append(Product(product_id, initial, **spec))
    return HostConfig(
        products=products,
        price_model=prices,
        # Demo customers only; competition customers are configured separately.
        customer_model=RandomCustomers(orders_per_interval=rfqs, quantities=(2, 4, 10),
                                       limit_offset=(0.03, 0.30)),
        interval_seconds=5.0, price_ticks_per_interval=10, final_hedge_seconds=2.0, seed=seed,
    )
