"""Starter submission: copy this file, rename it after your team, and fill in the logic sections.

Test it end to end against the demo exchange (from the kit folder, venv active):

    python demo/run_demo.py --bot demo/demo_participant.py
    python demo/run_demo.py --bot myteam_round1.py --opponents 2 --intervals 12
    python demo/run_demo.py --bot myteam_round1.py --check-only

The organiser runs only the Dealer class below. See PARTICIPANTS.md for the full rules and the exact
contents of `orders`, `market`, and `portfolio`.

Any parameters you estimate offline from the round's data files go in the constructor defaults
below: submissions may not read files.

Allowed imports: Python standard library, numpy, rfq.sdk. Every method must return within one
second and must never raise; an exception or timeout disconnects your dealer for the session.
"""
from rfq.sdk import round_lot, round_quote


class Dealer:
    def __init__(self, base_edge=0.10):
        # Every parameter needs a default; pass overrides with --params '{"base_edge": 0.08}'.
        # Edges are in price units: an ask is fair + edge, a bid is fair - edge.
        self.base_edge = base_edge
        # State persists for the whole session; keep any history you want here.
        self.wins = 0
        self.losses = 0
        self.expired = 0

    # ------------------------------------------------------------------------------------------
    # 1. FAIR VALUE: what do you think a product is worth right now?
    # ------------------------------------------------------------------------------------------
    def fair_value(self, product_id, market, portfolio):
        # === YOUR LOGIC HERE ===
        # Baseline: trust the exchange reference price.
        return market["prices"][product_id]

    # ------------------------------------------------------------------------------------------
    # 2. QUOTING: what price do you offer a customer, or do you decline (return None)?
    # ------------------------------------------------------------------------------------------
    def quote_price(self, order, fair, market, portfolio):
        # === YOUR LOGIC HERE ===
        # Baseline: the same fixed markup on every order. Return None to decline an order.
        if order["side"] == "BUY":       # customer buys, so you sell at your ask
            return fair + self.base_edge
        else:                            # customer sells, so you buy at your bid
            return fair - self.base_edge

    def quote(self, orders, market, portfolio):
        """Called each new interval and after every price move. Return {order_id: price or None}.

        The latest price per order replaces the previous one; None withdraws it. An order left out
        of the dict keeps whatever you quoted before.
        """
        quotes = {}
        for order in orders:
            fair = self.fair_value(order["product_id"], market, portfolio)
            price = self.quote_price(order, fair, market, portfolio)
            if price is None or price <= 0:
                # Declining. Map to None: leaving the order out would keep any earlier quote alive.
                quotes[order["order_id"]] = None
                continue
            tick = market["products"][order["product_id"]]["tick_size"]
            quotes[order["order_id"]] = round_quote(price, tick, order["side"])
        return quotes

    # ------------------------------------------------------------------------------------------
    # 3. HEDGING: which trades do you do against the exchange right now?
    # ------------------------------------------------------------------------------------------
    def hedge(self, market, portfolio):
        """Called after your customer fills and every price move, never while earlier trades are
        in flight. Return [(product_id, "BUY" | "SELL", quantity), ...]; trades execute at the
        latest exchange price."""
        trades = []
        for product_id, position in portfolio["positions"].items():
            product = market["products"][product_id]
            # === YOUR LOGIC HERE ===
            # Baseline: flatten every position straight away.
            quantity = round_lot(abs(position), product["lot_size"])
            if quantity and product["tradable_against_exchange"]:
                trades.append((product_id, "SELL" if position > 0 else "BUY", quantity))
        return trades

    # ------------------------------------------------------------------------------------------
    # 4. RESULTS (optional): every dealer sees every order's outcome.
    # ------------------------------------------------------------------------------------------
    def on_customer_result(self, result):
        # === YOUR LOGIC HERE ===
        # result: order_id, product_id, quantity, status ("filled"/"expired"),
        #         winner, price (the winning quote), traded (True if you won).
        # Baseline: count outcomes.
        if result["status"] == "expired":
            self.expired += 1
        elif result["traded"]:
            self.wins += 1
        else:
            self.losses += 1
