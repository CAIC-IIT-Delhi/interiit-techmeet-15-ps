"""Test your bot end to end against the demo exchange (demo_data.csv + demo_settlement_rules.csv).

From the kit folder, with the virtual environment active:

    python demo/run_demo.py                                      # the baseline bot vs 2 example dealers
    python demo/run_demo.py --bot myteam_round1.py               # your bot
    python demo/run_demo.py --bot myteam_round1.py --params '{"base_edge": 0.08}' --opponents 0 --intervals 6
    python demo/run_demo.py --bot myteam_round1.py --seed 7 --start-row 600   # different RFQs and price window
    python demo/run_demo.py --bot myteam_round1.py --check-only               # pre-flight check only

What it does:
  1. Pre-flight check: imports your file, builds Dealer(), and calls every method you define on
     sample data, in a separate process with hard time limits. It FAILS on anything the exchange
     would reject or the rules forbid, and WARNS about legal choices that lose money.
  2. Starts the demo exchange, which replays demo_data.csv and settles with
     demo_settlement_rules.csv. The first interval starts once every dealer is connected.
  3. Connects your bot as dealer_1, plus up to two example opponents.
  4. Prints the results table, a summary of how your bot did, and anything worth a look.

The demo replays the same prices, RFQs (for a given --seed) and opponents every run. If you fitted
your bot on demo_data.csv, a good demo result is in-sample: vary --seed and --start-row (on rows
you did not fit on) before trusting it.
"""
import argparse
import ast
import copy
import csv
from decimal import Decimal
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback

DEMO = Path(__file__).resolve().parent
ROOT = DEMO.parent
sys.path.insert(0, str(ROOT))

TICKS_PER_INTERVAL = 10
FINAL_WINDOW_SECONDS = 2.0
CALL_LIMIT_S, STARTUP_LIMIT_S = 1.0, 10.0
MAX_SAMPLE_PRODUCTS = 50
SENTINEL = "@@PREFLIGHT "
ALLOWED_TOP_LEVEL = set(getattr(sys, "stdlib_module_names", ())) | {"numpy", "rfq"}
# Standard-library modules that would break the no-I/O rule.
FORBIDDEN_STDLIB = {"socket", "subprocess", "threading", "multiprocessing", "concurrent", "asyncio",
                    "sqlite3", "urllib", "http", "ftplib", "smtplib", "shutil", "pathlib", "tempfile",
                    "glob", "pickle", "shelve", "ctypes", "importlib", "signal", "io"}
FILE_CALLS = {("os", name) for name in ("open", "fdopen", "listdir", "scandir", "walk", "remove", "unlink",
                                        "rename", "mkdir", "makedirs", "rmdir", "system", "popen", "chdir")}


def say(message=""):
    print(message, flush=True)


def fail(message):
    say(f"\n✗ {message}")
    sys.exit(1)


class Checks:
    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, message):
        self.errors.append(message)
        say(f"  ✗ {message}")

    def warn(self, message):
        self.warnings.append(message)
        say(f"  ! {message}")


# --------------------------------------------------------------------------------------------
# 1. Pre-flight check (runs in a child process: python run_demo.py --preflight-child ...)
# --------------------------------------------------------------------------------------------
def static_checks(path, checks):
    """Syntax for Python 3.11, imports outside stdlib/numpy/rfq.sdk, I/O modules, file access."""
    source = path.read_text()
    try:
        tree = ast.parse(source, filename=str(path), feature_version=(3, 11))
    except SyntaxError as exc:
        caret = " " * max((exc.offset or 1) - 1, 0) + "^"
        fail(f"{path.name} line {exc.lineno}: {exc.msg}\n    {(exc.text or '').rstrip()}\n    {caret}\n"
             f"(submissions run on Python 3.11, so syntax newer than 3.11 is also an error)")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                check_import(alias.name, node.lineno, checks)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            check_import(node.module, node.lineno, checks)
            if node.module == "rfq":
                for alias in node.names:
                    if alias.name != "sdk":
                        checks.error(f"line {node.lineno}: from rfq import {alias.name} is not allowed "
                                     f"(only rfq.sdk)")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in ("open", "__import__", "exec", "eval", "compile"):
                checks.error(f"line {node.lineno}: {func.id}() is not allowed: submissions cannot read files or "
                             f"load code dynamically. Put the numbers you estimated offline into your code")
            elif (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                  and (func.value.id, func.attr) in FILE_CALLS):
                checks.error(f"line {node.lineno}: {func.value.id}.{func.attr}() is not allowed: no file or "
                             f"process access")


def check_import(name, lineno, checks):
    top = name.split(".")[0]
    if top not in ALLOWED_TOP_LEVEL or (top == "rfq" and name not in ("rfq", "rfq.sdk")):
        checks.error(f"line {lineno}: import {name} is not allowed (standard library, numpy and rfq.sdk only; "
                     f"to request a module, ask the organisers)")
    elif top in FORBIDDEN_STDLIB:
        checks.error(f"line {lineno}: import {name} is not allowed (no files, network, threads, processes or "
                     f"dynamic imports)")


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def on_grid(value, step):
    return Decimal(str(value)) % Decimal(str(step)) == 0


def user_traceback():
    """The current exception's traceback without this script's own frames."""
    kind, value, tb = sys.exc_info()
    frames = [f for f in traceback.extract_tb(tb) if Path(f.filename).resolve() != Path(__file__).resolve()]
    text = "".join(traceback.format_list(frames)) + "".join(traceback.format_exception_only(kind, value))
    if isinstance(value, TypeError) and "required positional argument" in str(value) and "__init__" in str(value):
        text += "Hint: every parameter of Dealer.__init__ needs a default value, e.g. def __init__(self, edge=0.1)"
    return text.rstrip()


class CallTimeout(Exception):
    pass


def _alarm(*_):
    raise CallTimeout


def timed(label, call, limit=CALL_LIMIT_S, hint=None):
    """Run one bot call with a hard time limit; any failure, including an exit, stops the check."""
    use_alarm = hasattr(signal, "setitimer")
    if use_alarm:
        signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, limit + 0.25)
    start = time.perf_counter()
    try:
        result = call()
    except CallTimeout:
        fail(f"{label} took longer than {limit:g} s (the limit); it never returned")
    except SystemExit as exc:
        fail(f"{label} called sys.exit({exc.code!r}) or raised SystemExit. Never exit from your bot: "
             f"return normally, or return None/[] to do nothing")
    except KeyboardInterrupt:
        fail(f"{label} raised KeyboardInterrupt. Never raise it from your bot")
    except Exception:
        text = user_traceback()
        if hint and isinstance(sys.exc_info()[1], TypeError) and "positional argument" in str(sys.exc_info()[1]):
            text += f"\n{hint}"
        fail(f"{label} raised an exception (this would stop your bot):\n{text}")
    finally:
        if use_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
    elapsed = time.perf_counter() - start
    if elapsed > limit:
        fail(f"{label} took {elapsed:.2f} s; the limit is {limit:g} s")
    if elapsed > limit / 4:
        say(f"  ! {label} took {elapsed:.2f} s. quote() and hedge() run back to back on every price update "
            f"(every 0.5 s), so keep each well under 0.25 s")
    return result


def encodable(label, value, checks):
    from rfq.store import encode
    try:
        encode(value)
    except (TypeError, ValueError) as exc:
        checks.error(f"{label} returned something that cannot be sent to the exchange ({exc}). Use plain Python "
                     f"numbers, e.g. float(x) for numpy values, and never NaN or infinity")
        return False
    return True


def check_quotes(quotes, orders_by_id, prices, settled, products, checks, label="quote()"):
    if quotes is not None and not isinstance(quotes, dict):
        fail(f"{label} must return a dict {{order_id: price or None}}, got {type(quotes).__name__}")
    live = 0
    for oid, price in (quotes or {}).items():
        order = orders_by_id.get(oid)
        if order is None:
            checks.error(f"{label} quoted unknown order id {oid!r}; the exchange would reject it (UNKNOWN_ORDER)")
            continue
        if price is None:
            continue
        if not finite_number(price) or not price > 0:
            checks.error(f"{label} price for {oid} must be a positive finite number or None, got {price!r}")
            continue
        live += 1
        key = order["product_id"]
        tick = products[key]["tick_size"]
        if not on_grid(float(price), tick):
            checks.error(f"{label} price {price} for {oid} is not on the {tick} tick; the exchange would reject "
                         f"it (BAD_TICK). Use round_quote(price, tick_size, order['side'])")
        # Fair could be the exchange price or the settlement value: only flag quotes beyond both.
        low, high = min(prices[key], settled[key]), max(prices[key], settled[key])
        if order["side"] == "BUY" and price < low:
            checks.warn(f"{label} offers {key} at {price} to a buying customer, below both its exchange price "
                        f"{prices[key]:.4f} and settlement value {settled[key]:.4f}: you'd sell below the market "
                        f"(order['side'] is the CUSTOMER's side)")
        if order["side"] == "SELL" and price > high:
            checks.warn(f"{label} bids {price} for {key} to a selling customer, above both its exchange price "
                        f"{prices[key]:.4f} and settlement value {settled[key]:.4f}: you'd buy above the market "
                        f"(order['side'] is the CUSTOMER's side)")
    return live


def check_trades(label, trades, products, checks):
    from rfq.sdk import trades_action
    if trades is not None and not isinstance(trades, (list, tuple)):
        fail(f"{label} must return a list of trades or None, got {type(trades).__name__}"
             + (". To send one trade, wrap it in a list: [(product_id, side, quantity)]"
                if isinstance(trades, (str, dict)) else ""))
    for item in trades or ():
        raw = item if isinstance(item, dict) else dict(zip(("product_id", "side", "quantity", "limit_price"),
                                                          item if isinstance(item, (list, tuple)) else ()))
        quantity = raw.get("quantity")
        if quantity is not None and quantity != 0 and (not finite_number(quantity) or quantity < 0):
            checks.error(f"{label} quantity {quantity!r} must be a positive finite number "
                         f"(use side 'SELL' rather than a negative quantity)")
            return 0
        if raw.get("limit_price") is not None and not (finite_number(raw["limit_price"]) and raw["limit_price"] > 0):
            checks.error(f"{label} limit_price {raw['limit_price']!r} must be a positive finite number")
            return 0
    try:
        batch = trades_action(trades)
    except Exception:
        fail(f"{label} returned trades in an unknown format: {trades!r}. Use (product_id, 'BUY' or 'SELL', quantity)")
    for item in (batch[0]["trades"] if batch else []):
        key = item.get("product_id")
        if key not in products or item.get("side") not in ("BUY", "SELL"):
            fail(f"{label} trade {item} needs a known product_id and side 'BUY' or 'SELL' (upper case)")
        if not on_grid(item["quantity"], products[key]["lot_size"]):
            checks.error(f"{label} quantity {item['quantity']} {key} is not a whole lot; the exchange would reject "
                         f"it (BAD_LOT). Use round_lot(quantity, lot_size)")
        if not products[key]["tradable_against_exchange"]:
            checks.error(f"{label} trades {key}, which can't be traded on the exchange (NOT_TRADABLE). Check "
                         f"market['products'][product]['tradable_against_exchange']")
    return len(batch[0]["trades"]) if batch else 0


def preflight_child(bot_path, params):
    from dataclasses import asdict
    from demo.demo_exchange import build
    from rfq.engine import Engine
    from rfq.models import settle_with
    from rfq.sdk import Customers, Exchange
    from rfq.store import Store

    say(f"Pre-flight check of {bot_path}")
    checks = Checks()
    static_checks(bot_path, checks)
    if checks.errors:
        # Never run code that breaks the rules: it might touch files or processes.
        fail(f"pre-flight found {len(checks.errors)} problem(s) in the source (listed above); the bot was not run")
    config = build()
    store = Store(":memory:")
    Engine(config, store)       # same validation as the exchange
    store.close()
    # Like the competition: the bot runs in an empty folder.
    workdir = tempfile.mkdtemp(prefix="rfq_check_")
    os.chdir(workdir)
    spec = importlib.util.spec_from_file_location("submitted_dealer", bot_path)
    module = importlib.util.module_from_spec(spec)
    timed(f"importing {bot_path.name}", lambda: spec.loader.exec_module(module), STARTUP_LIMIT_S)
    dealer_class = getattr(module, "Dealer", None)
    if dealer_class is None:
        fail(f"{bot_path.name} has no class named Dealer")
    if not isinstance(dealer_class, type):
        fail(f"Dealer must be a class (class Dealer: ...), but it's a {type(dealer_class).__name__}")
    for method in ("quote", "hedge"):
        attribute = dealer_class.__dict__.get(method)
        if isinstance(attribute, property):
            fail(f"Dealer.{method} is a property; it must be a method: def {method}(self, ...)")
        if not callable(getattr(dealer_class, method, None)):
            fail(f"Dealer has no {method}() method")
    dealer = timed(f"Dealer(**{params})", lambda: dealer_class(**params), STARTUP_LIMIT_S,
                   hint="(check that every key in --params matches a parameter of Dealer.__init__)")

    def still_has_hedge(after):
        if not callable(getattr(dealer, "hedge", None)):
            fail(f"after {after}, dealer.hedge is no longer a method: an attribute named self.hedge replaced it. "
                 f"Rename the attribute (e.g. self.hedging)")
    still_has_hedge("Dealer()")

    products = {p.product_id: asdict(p) for p in config.products}
    rows = config.price_model.rows
    prices = dict(rows[min(3, len(rows) - 1)])     # off-grid prices, like live ones
    settled = settle_with(config.price_model.settlements, prices)
    sample_products = list(products)[:MAX_SAMPLE_PRODUCTS]
    orders = [{"order_id": f"check-{key}-{side}", "customer_id": "customer-1", "product_id": key,
               "side": side, "quantity": 2, "expires_interval": 1}
              for key in sample_products for side in ("BUY", "SELL")]
    orders_by_id = {o["order_id"]: o for o in orders}

    def market(closing=False):
        return {"type": "market", "interval_id": 1, "price_version": 4, "prices": dict(prices),
                "interval_seconds": config.interval_seconds, "closes_at": time.time() + config.interval_seconds,
                "closing": closing, "active_orders": [] if closing else copy.deepcopy(orders),
                "products": copy.deepcopy(products)}

    def portfolio(positions):
        return {"type": "portfolio", "cash": config.starting_cash, "positions": dict(positions),
                "equity": config.starting_cash, "pnl": 0.0, "customer_trades": 0, "exchange_trades": 0,
                "customer_notional": 0.0, "exchange_notional": 0.0, "fees_paid": 0.0, "penalties_paid": 0.0,
                "interval_id": 1, "price_version": 4}

    flat = {k: 0.0 for k in products}
    first = next(iter(products))
    beyond = {**flat, first: float(products[first]["max_long"]) + 5 if products[first].get("max_long") is not None
              else 100.0}

    quotes = timed("quote()", lambda: dealer.quote(copy.deepcopy(orders), market(), portfolio(flat)))
    still_has_hedge("quote()")
    live = check_quotes(quotes, orders_by_id, prices, settled, products, checks)
    encodable("quote()", quotes, checks)
    say(f"  ✓ quote() priced {live} of {len(orders)} sample orders" + ("" if live else
        " (it declined all of them; a bot that never quotes earns no customer points)"))

    scenarios = (("with no positions", flat, False), ("after a customer fill", {**flat, first: 2.0}, False),
                 ("beyond a position limit", beyond, False), ("in the final window", {**flat, first: 2.0}, True))
    for label, positions, closing in scenarios:
        trades = timed(f"hedge() {label}", lambda: dealer.hedge(market(closing), portfolio(positions)))
        still_has_hedge(f"hedge() {label}")
        count = check_trades(f"hedge() {label}", trades, products, checks)
        encodable(f"hedge() {label}", trades, checks)
        say(f"  ✓ hedge() {label}: {count} trades")

    # Optional callbacks, called exactly as the runner calls them.
    sample_fill = {"type": "fill", "dealer_id": "dealer_1", "source": "customer", "counterparty": "customer",
                   "product_id": first, "side": "BUY", "quantity": 2, "price": prices[first], "fee": 0.0,
                   "order_id": orders[0]["order_id"], "customer_id": "customer-1", "interval_id": 1,
                   "price_version": 4}
    exchange_fill = {"type": "fill", "dealer_id": "dealer_1", "source": "exchange", "counterparty": "exchange",
                     "product_id": first, "side": "SELL", "quantity": 2.0, "price": prices[first],
                     "fee": 0.02, "action_id": "check:0", "interval_id": 1, "price_version": 4}
    sample_result = {"type": "customer_result", "order_id": orders[0]["order_id"], "interval_id": 1,
                     "product_id": first, "quantity": 2, "status": "filled", "winner": "dealer_2",
                     "price": prices[first], "traded": False}
    sample_ack = {"type": "ack", "action_id": "check", "status": "accepted"}
    calls = (("on_market", "", lambda a: (market(), portfolio(flat), Exchange(a))),
             ("on_customer_order", "", lambda a: (copy.deepcopy(orders[0]), market(), Customers(a))),
             ("on_fill", "", lambda a: (dict(sample_fill), portfolio({**flat, first: 2.0}), Exchange(a))),
             ("on_fill", " (exchange fill)", lambda a: (dict(exchange_fill), portfolio(flat), Exchange(a))),
             ("on_timer", "", lambda a: (market(), portfolio(flat), Exchange(a), Customers(a))),
             ("on_customer_result", "", lambda a: (dict(sample_result),)),
             ("on_ack", "", lambda a: (dict(sample_ack),)))
    for name, suffix, make_args in calls:
        method = getattr(dealer, name, None)
        if not callable(method):
            continue
        actions = []
        args = make_args(actions)
        timed(f"{name}(){suffix}", lambda: method(*args),
              hint=f"(the runner calls {name} with {len(args)} arguments after self; see PARTICIPANTS.md §6)")
        still_has_hedge(f"{name}()")
        encodable(f"{name}(){suffix}", actions, checks)
        say(f"  ✓ {name}(){suffix} ran")

    if checks.errors:
        fail(f"pre-flight found {len(checks.errors)} problem(s) that the exchange would reject or the rules forbid "
             f"(listed above). Fix them, or use --skip-check to run anyway")
    note = f" with {len(checks.warnings)} warning(s) about money-losing choices (listed above)" if checks.warnings else ""
    say(f"  ✓ pre-flight passed{note}")
    say(SENTINEL + json.dumps({"warnings": checks.warnings}))


def preflight(bot, params, env):
    """Run the pre-flight check in a child process, so a bot that hangs or exits can't take run_demo down."""
    command = [sys.executable, "-u", str(Path(__file__).resolve()), "--preflight-child",
               "--bot", str(bot), "--params", json.dumps(params)]
    limit = STARTUP_LIMIT_S * 2 + CALL_LIMIT_S * 20 + 10
    child = subprocess.Popen(command, cwd=ROOT, env={**env, "RFQ_DEMO_START": "0"},
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines, result = [], None
    def read():
        for line in child.stdout:
            lines.append(line)
            if line.startswith(SENTINEL):
                continue
            say(line.rstrip())
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        child.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
        reader.join(timeout=1)
        last = next((l.strip() for l in reversed(lines) if l.strip()), "the start")
        fail(f"pre-flight stopped responding (your bot may be stuck in a loop) after: {last}")
    reader.join(timeout=2)
    for line in lines:
        if line.startswith(SENTINEL):
            result = json.loads(line[len(SENTINEL):])
    if child.returncode == 0 and result is not None:
        say("")
        return result["warnings"]
    if child.returncode == 1:
        sys.exit(1)          # the child already printed the reason
    last = next((l.strip() for l in reversed(lines) if l.strip() and not l.startswith(SENTINEL)), "the start")
    fail(f"your bot ended the pre-flight process unexpectedly (exit code {child.returncode}; e.g. os._exit() or a "
         f"crash) after: {last}. Never exit or kill the process from your bot")


# --------------------------------------------------------------------------------------------
# 2-4. Run the session
# --------------------------------------------------------------------------------------------
def count_rejected_items(lines):
    items, codes = 0, {}
    for line in lines:
        if "PARTIAL REJECT " in line:
            try:
                errors = ast.literal_eval(line.split("PARTIAL REJECT ", 1)[1].strip())
            except (ValueError, SyntaxError):
                continue
            for code in errors.values():
                items += 1
                codes[code] = codes.get(code, 0) + 1
        elif "REJECT " in line:
            code = line.split("REJECT ", 1)[1].split(":", 1)[0].strip()
            items += 1
            codes[code] = codes.get(code, 0) + 1
    return items, codes


def customer_edge(trades_path, dealer="dealer_1"):
    """Markup earned on customer trades: (price - exchange price) in the dealer's favour."""
    edge = 0.0
    with open(trades_path, newline="") as handle:
        for row in csv.DictReader(handle):
            if row["dealer_id"] == dealer and row["counterparty"] == "customer":
                sign = 1 if row["side"] == "SELL" else -1
                edge += sign * (float(row["price"]) - float(row["reference_price"])) * float(row["quantity"])
    return edge


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bot", default=str(DEMO / "demo_participant.py"), help="your submission file")
    p.add_argument("--params", default="{}", help="JSON arguments for your Dealer, e.g. '{\"base_edge\": 0.08}'")
    p.add_argument("--opponents", type=int, default=2, choices=(0, 1, 2),
                   help="example dealers to compete against")
    p.add_argument("--intervals", "--rounds", dest="intervals", type=int, default=12,
                   help="RFQ intervals before settlement (demo data has 120)")
    p.add_argument("--interval", type=float, default=None, help="seconds per interval (default 5)")
    p.add_argument("--seed", type=int, default=42, help="seed for the demo customers' RFQs and the opponents")
    p.add_argument("--start-row", type=int, default=0, help="first data row to replay, counting from 0")
    p.add_argument("--rfqs", type=int, default=3, help="customer RFQs per interval (default 3)")
    p.add_argument("--products", default="{}",
                   help="JSON per-product settings, e.g. '{\"C\": {\"max_long\": 10, \"fee_per_unit\": 0.02}}'")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--data", default=str(DEMO / "demo_data.csv"))
    p.add_argument("--rules", default=str(DEMO / "demo_settlement_rules.csv"))
    p.add_argument("--output", default=str(ROOT / "run_demo"), help="folder for results.json, trades.csv, audit.sqlite")
    p.add_argument("--skip-check", action="store_true", help="skip the pre-flight check")
    p.add_argument("--check-only", action="store_true", help="run the pre-flight check and stop")
    p.add_argument("--preflight-child", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()
    bot = Path(args.bot).resolve()

    if args.preflight_child:
        preflight_child(bot, json.loads(args.params))
        return

    # Treat termination like Ctrl+C, so child processes are always cleaned up.
    def interrupt(*_):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), interrupt)

    if args.intervals < 1:
        p.error("--intervals must be at least 1")
    if args.rfqs < 0:
        p.error("--rfqs must be 0 or more")
    if sys.version_info[:2] != (3, 11):
        say(f"! You are running Python {sys.version.split()[0]}; submissions are run on Python 3.11. "
            f"Pre-flight rejects syntax newer than 3.11.\n")
    output = Path(args.output).resolve()
    if not bot.is_file():
        fail(f"bot file not found: {bot}")
    for flag, text in (("--params", args.params), ("--products", args.products)):
        try:
            assert isinstance(json.loads(text), dict)
        except (ValueError, AssertionError):
            fail(f"{flag} must be a JSON object, e.g. '{{\"base_edge\": 0.08}}', got {text!r}")
    params = json.loads(args.params)
    data, rules = Path(args.data).resolve(), Path(args.rules).resolve()
    env = {**os.environ, "RFQ_DEMO_DATA": str(data), "RFQ_DEMO_RULES": str(rules),
           "RFQ_DEMO_START": str(args.start_row), "RFQ_DEMO_SEED": str(args.seed),
           "RFQ_DEMO_PRODUCTS": args.products, "RFQ_DEMO_RFQS": str(args.rfqs), "PYTHONUNBUFFERED": "1"}
    os.environ.update({k: v for k, v in env.items() if k.startswith("RFQ_DEMO_")})
    try:
        from demo.demo_exchange import build
        from rfq.engine import Engine
        from rfq.store import Store
        config = build()
        store = Store(":memory:")
        Engine(config, store)
        store.close()
        total_rows = len(config.price_model.all_rows)
    except Exception as exc:
        fail(f"the demo exchange cannot use {data.name} / {rules.name}: {exc}")
    warnings = [] if args.skip_check else preflight(bot, params, env)
    if args.check_only:
        say("Pre-flight only (--check-only): not starting a session.")
        return

    seconds_per_interval = args.interval or 5.0
    window_updates = max(round(FINAL_WINDOW_SECONDS / (seconds_per_interval / TICKS_PER_INTERVAL)) - 1, 0)
    needed = args.intervals * TICKS_PER_INTERVAL + window_updates + 1
    first_row, last_row = args.start_row + 1, args.start_row + needed
    url = f"ws://127.0.0.1:{args.port}"
    dealers = 1 + args.opponents
    exchange_cmd = [sys.executable, "-u", "-m", "rfq.exchange", "--config", "demo.demo_exchange",
                    "--rounds", str(args.intervals), "--port", str(args.port), "--output", str(output),
                    "--wait-for-dealers", str(dealers), "--start-timeout", "30"]
    if args.interval:
        exchange_cmd += ["--interval", str(args.interval)]
    seconds = args.intervals * seconds_per_interval + FINAL_WINDOW_SECONDS
    say(f"Starting demo exchange: {args.intervals} intervals (~{seconds:.0f} s), seed {args.seed}, "
        f"{args.rfqs} RFQs per interval.")
    approx = "about " if args.interval else ""
    say(f"Replaying {approx}rows {first_row}-{min(last_row, total_rows)} of {total_rows} in {data.name}; "
        f"settling with {rules.name}.")
    if last_row > total_rows:
        say(f"! The session needs {needed} rows but only {total_rows - args.start_row} remain after --start-row "
            f"{args.start_row}: prices will FREEZE for the last {last_row - total_rows} updates, which makes "
            f"trading against the known settlement risk-free. Use --start-row {max(total_rows - needed, 0)} or less.")
    say("Note: the same rows, RFQs (for this seed) and opponents replay every run. If your bot was fitted on this "
        "file, this is an in-sample test: also try other --seed values, and --start-row on rows you didn't fit on.")
    if args.interval:
        say(f"Note: with {args.interval:g}-second intervals the 2-second final window holds more price updates, "
            f"so settlement prices differ from a 5-second run.")
    processes = []
    try:
        exchange = subprocess.Popen(exchange_cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
        processes.append(exchange)
        startup = []
        for line in exchange.stdout:
            startup.append(line)
            if line.startswith("READY"):
                break
        else:
            text = "".join(startup)
            if "address already in use" in text.lower() or "errno 48" in text.lower() or "errno 98" in text.lower():
                fail(f"port {args.port} is already in use (another run?). Try --port {args.port + 1}")
            last = [l for l in text.strip().splitlines() if l.strip()][-1:] or ["(no output)"]
            fail(f"the demo exchange did not start: {last[0]}\n\nFull output:\n{text}")

        runner = [sys.executable, "-u", "-m", "rfq.runner", "--url", url]
        bot_process = subprocess.Popen(runner + ["--file", str(bot), "--id", "dealer_1", "--token", "local-1",
                                                 "--params", json.dumps(params)],
                                       cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True)
        processes.append(bot_process)
        for i in range(args.opponents):
            opponent = {"edge_range": [0.03, 0.20], "seed": args.seed + i + 2}
            processes.append(subprocess.Popen(
                runner + ["--file", str(ROOT / "examples" / "dealer.py"), "--id", f"dealer_{i + 2}",
                          "--token", f"local-{i + 2}", "--params", json.dumps(opponent)],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        say(f"Your bot is dealer_1; opponents: {', '.join(f'dealer_{i + 2}' for i in range(args.opponents)) or 'none'}. "
            f"Interval 1 starts when all {dealers} have connected.\n")

        bot_lines = []
        def relay():
            for line in bot_process.stdout:
                bot_lines.append(line)
                say(f"  {line.rstrip()}")
        reader = threading.Thread(target=relay, daemon=True)
        reader.start()
        exchange_lines = [line for line in exchange.stdout]
        exchange.wait()
        for process in processes[1:]:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
        reader.join(timeout=2)
    except KeyboardInterrupt:
        say("\nInterrupted.")
        sys.exit(130)
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    say("".join(line for line in exchange_lines
                if not line.startswith(("RESULTS", "TRADES", "START", "WAITING"))).rstrip())
    results_path = output / "results.json"
    if not results_path.is_file():
        fail("the session did not finish; see the output above")
    results = json.loads(results_path.read_text())
    if results.get("status") != "completed":
        fail(f"the EXCHANGE stopped the session ({results.get('status')}): {results.get('error', 'no details')}. "
             f"This is a problem with the data, rules or settings, not necessarily your bot.")
    mine = results["results"].get("dealer_1")
    say("\nYour bot (dealer_1)")
    if mine is None:
        fail("your bot never connected to the exchange; see its output above")
    ranking = sorted(results["results"], key=lambda name: -results["results"][name]["pnl"])
    rejected, codes = count_rejected_items(bot_lines)
    finished = any("FINAL PnL" in line for line in bot_lines)
    stopped = next((line.strip() for line in bot_lines if "BOT STOPPED" in line or "SESSION PROBLEM" in line), None)
    with sqlite3.connect(output / "audit.sqlite") as db:
        rows = db.execute("select request, response from actions where dealer_id='dealer_1' and session_id=?",
                          (results["session_id"],)).fetchall()
    requests = [json.loads(r) for r, _ in rows]
    late = sum(1 for _, a in rows if json.loads(a).get("code") in ("STALE_QUOTE", "STALE_PRICE"))
    quoted = sum(1 for r in requests if r.get("type") == "quotes" for v in r["quotes"].values() if v is not None)
    edge = customer_edge(output / "trades.csv")
    held = {k: v for k, v in mine["positions"].items() if v}
    shown = dict(list(held.items())[:12])
    more = f" ... and {len(held) - 12} more" if len(held) > 12 else ""
    settle_shown = {k: round(results["prices"][k], 4) for k in shown}
    say(f"  final PnL        {mine['pnl']:+.4f}   (rank {ranking.index('dealer_1') + 1} of {len(ranking)})")
    say(f"  customer orders  {mine['customer_trades']} won   (quote updates sent: {quoted}; markup earned "
        f"{edge:+.4f})")
    say(f"  exchange trades  {mine['exchange_trades']}")
    say(f"  fees / penalties {mine['fees_paid']:.4f} / {mine['penalties_paid']:.4f}")
    say(f"  settlement       {'flat (no positions)' if not held else f'positions {shown}{more} at settlement prices {settle_shown}'}")
    say(f"  rejected items   {rejected}" + (f"  ({', '.join(f'{c} x{n}' for c, n in codes.items())})" if rejected else ""))
    say(f"  late messages    {late}  (quotes or trades that arrived after their interval closed or prices moved on)")
    say(f"  outputs          {output / 'trades.csv'} (every trade), {output / 'audit.sqlite'} (every message)")

    if stopped or not finished or bot_process.returncode:
        fail(f"your bot stopped before the session ended. {stopped or 'See its output above.'}")
    issues = list(warnings)
    if mine["pnl"] < 0:
        issues.append(f"your PnL is negative ({mine['pnl']:+.4f}); in the competition, teams with non-positive "
                      f"average PnL get no customer volume or order points")
    if mine["customer_trades"] == 0:
        issues.append("you won no customer orders" + (" (you never sent a quote)" if not quoted else
                      " (your quotes were never the best eligible price)"))
    if rejected:
        issues.append(f"{rejected} of your quotes/trades were rejected ({', '.join(codes)})")
    if late > 5:
        issues.append(f"{late} of your messages arrived late; your calls may be too slow for 0.5-second updates")
    if mine["penalties_paid"] > 0:
        issues.append(f"you paid {mine['penalties_paid']:.2f} in position-limit penalties; check positions against "
                      f"max_long/max_short in market['products']")
    if edge > 0 and mine["fees_paid"] > edge:
        issues.append(f"exchange fees ({mine['fees_paid']:.2f}) were larger than the markup your customer trades "
                      f"earned ({edge:.2f}); you may be hedging or trading the exchange too often")
    if issues:
        say(f"\n⚠ Your bot ran the whole session, but {len(issues)} thing(s) need a look:")
        for issue in issues:
            say(f"  - {issue}")
    else:
        say("\n✓ Your bot connected, quoted, traded, stayed up for the whole session, and made money.")


if __name__ == "__main__":
    main()
