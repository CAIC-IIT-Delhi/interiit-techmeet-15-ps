# Participant guide: building your dealer bot

This guide is everything you need to build a dealer bot from scratch:
- what the game is, and the data you're given
- how a session runs, what your bot receives and sends, and the rules the exchange enforces
- how you're scored, and how to test locally

Read sections 1–5 before writing any code. The rest is reference.

**Contents**

1. [The game in one page](#1-the-game-in-one-page)
2. [Setup and your first run](#2-setup-and-your-first-run)
3. [The data you're given: `data.csv` and `settlement_rules.csv`](#3-the-data-youre-given)
4. [Submission requirements](#4-submission-requirements)
5. [How a session runs](#5-how-a-session-runs)
6. [Your bot's interface](#6-your-bots-interface)
7. [Data reference](#7-data-reference)
8. [Rules the exchange enforces](#8-rules-the-exchange-enforces)
9. [Scoring](#9-scoring)
10. [What you can and can't see](#10-what-you-can-and-cant-see)
11. [Testing, debugging and reading results](#11-testing-debugging-and-reading-results)
12. [Pitfalls and FAQ](#12-pitfalls-and-faq)
13. [Glossary](#13-glossary)

**Two words to keep apart:**
- A **round** is one of the competition's nine rounds (Round 1 on 22 Oct, and so on). Each round is evaluated over many **sessions**.
- An **interval** is one 5-second RFQ cycle inside a session: RFQs go out, dealers quote, the RFQs are matched. A session is a sequence of intervals.

---

## 1. The game in one page

You run a **dealer**: a market maker that customers come to when they want to trade.

- **Customers send requests for quote (RFQs).** For example: "I want to **buy 4 B**. What's your price?" Every dealer sees the same RFQ and privately sends back one price.
- **The best price wins, if the customer accepts it.**
  - For a customer **buy**, the **lowest** ask wins. For a customer **sell**, the **highest** bid wins.
  - Each customer has a **hidden limit**, and won't trade at a price too far from the exchange price.
  - If no quote is acceptable, nobody trades.
- **Winning gives you a position.** If you sold 4 B to a customer, you are now **short 4 B**. If B's price rises, you lose money.
- **You can trade on the exchange at any time,** at its current price plus a small fee, to reduce (hedge) or change your positions.
- **At the end of the session, every position is valued at a settlement price,** following published rules. Your **score is your profit and loss (PnL)**.

**So your bot is two pieces of logic:**

1. **`quote()`:** which customer requests to price, and at what price. Too wide and you lose the trade. Too tight and you win but earn nothing, or lose once the price moves.
2. **`hedge()`:** what to trade on the exchange, and when, to manage the risk your customer trades create.

**Before the competition** you get two files: a price history (`data.csv`) and the settlement rules (`settlement_rules.csv`). Use them **offline** to work out how the products move and relate, then build that knowledge into your bot.

The runner handles everything else: networking, timing, reconnects, message formats. You write one plain Python class, with no network code.

---

## 2. Setup and your first run

You need Python 3.11 or later. Submissions are run on **3.11**, so don't rely on anything newer.

```bash
cd <the kit folder>                  # the folder containing README.md
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### The `demo/` folder

| File | What it is |
| --- | --- |
| `demo/demo_participant.py` | baseline bot and template: copy it and fill in your logic |
| `demo/run_demo.py` | **one command to test your bot end to end** |
| `demo/demo_data.csv` | demo price history (see §3) |
| `demo/demo_data_holdout.csv` | a second history from the same model, with a different path: keep it for testing, don't tune on it |
| `demo/demo_settlement_rules.csv` | demo settlement rules (see §3) |
| `demo/demo_exchange.py` | the demo exchange: replays a price history and settles with a rules file |

### Test your bot with `run_demo.py`

```bash
cp demo/demo_participant.py myteam_round1.py          # start from the template (see the naming rule in §4)

python demo/run_demo.py --bot myteam_round1.py        # ~1 minute: your bot vs 2 example dealers, 12 intervals
```

The script does four things:

1. **Pre-flight check.** It imports your file, builds `Dealer()`, and calls `quote()`, `hedge()` and every optional `on_*` method you define, on sample data and in several situations: no positions, after a fill, beyond a limit, and in the final window.
   - **It runs in a separate process with hard time limits,** so a bot that hangs, calls `sys.exit()` or crashes is reported clearly.
   - **It stops with an error (✗)** if your bot would break the rules or be rejected:
     - syntax newer than Python 3.11
     - an import outside the standard library, numpy and `rfq.sdk` (including `from rfq import engine`), or a file, network, thread, process or dynamic-import module
     - a call to `open()`, `os.open()`, `os.listdir()`, `__import__()`, `exec()` and similar. A bot that breaks these rules is not run at all
     - no `Dealer` class, no `quote()`/`hedge()` method, or an attribute named `self.hedge` that hides the method
     - a method that raises, exits, never returns, is too slow, has the wrong signature, or returns the wrong type
     - `Dealer` that isn't a class, or `quote`/`hedge` defined as properties
     - a quote that isn't on the tick, or quotes an unknown order
     - a trade quantity that isn't a whole lot, or a side other than `"BUY"`/`"SELL"`
     - a value that can't be sent to the exchange: a numpy number, NaN, infinity, or a zero or negative quantity
     - a trade in a product that can't be traded on the exchange
   - **It warns (!)** about legal choices that lose money, e.g. quoting a price beyond both a product's exchange price and its settlement value.
2. **Starts the demo exchange.** It replays the price history and settles with the rules file, with demo settings for products, limits, fees, customers and opponents. Products come from the data file's columns, so you can practise with your own files (see §11).
3. **Runs a session.** Your bot is `dealer_1`, against up to two example opponents, and its output is shown as it runs.
   - Interval 1 starts once every dealer has connected.
   - A call slower than 0.5 s prints `SLOW: …`.
   - A bot that falls behind skips stale price updates and works on the newest prices.
   - If your bot crashes or is too slow, you see `BOT STOPPED: …` with the reason and your own code's traceback.
   - A connection or login problem shows `SESSION PROBLEM: …`.
4. **Reports.** The results table, then a summary of your bot:
   - final PnL and rank
   - customer and exchange trades
   - fees and penalties
   - settlement positions and prices
   - rejected items, by code
   - where the output files are

   If everything looks healthy, it ends with `✓ Your bot connected, quoted, traded, and stayed up for the whole session.` Otherwise it lists what needs a look, e.g. no customer orders won, rejections, penalties, or fees larger than your PnL. If your bot stopped, it exits with an error.

**⚠ The demo is the same every time.** It replays the same rows of `demo_data.csv`, with the same RFQs (for a given `--seed`) and the same opponents.
- If you tuned your bot on `demo_data.csv`, a good demo result is **in-sample** and says little about the live rounds.
- Rows you fitted on are in-sample **whatever `--start-row` you pick**. For an honest test, tune on `demo_data.csv` and test on the **held-out file**: `--data demo/demo_data_holdout.csv`. It comes from the same model with a different path, like the live rounds will. Alternatively, fit on one part of a file and test on another.
- One session of 12 intervals uses 124 rows, so 1,201 rows hold only about 9 separate sessions. A single session's PnL is noisy: compare the **average over several sessions**, using different `--seed` values too.
- The script prints which rows it replays. It warns if `--start-row` is so close to the end that prices would freeze (counting starts at 0).

| Option | Default | Meaning |
| --- | --- | --- |
| `--bot FILE` | `demo/demo_participant.py` | your submission |
| `--params JSON` | `{}` | constructor arguments; the names must match your `Dealer.__init__`. For the template: `'{"base_edge": 0.08}'` |
| `--opponents N` | 2 | example dealers to compete against (0–2). They quote a random markup of 0.03–0.20 per order and immediately flatten every position |
| `--intervals N` | 12 | intervals before settlement (`--rounds` also works). The demo data has 120; after that, prices hold at the last row |
| `--interval S` | 5 | seconds per interval; e.g. 1 for quicker iterations. The final window stays 2 s, so it then holds more price updates and settlement differs |
| `--seed N` | 42 | changes the demo customers' RFQs and the opponents' choices |
| `--start-row N` | 0 | replay from row N of the data file instead of the first row |
| `--data`, `--rules` | the demo files | replay your own CSVs in the same format, with any products (see §11) |
| `--products JSON` | `{}` | per-product settings for your own files, e.g. `'{"C": {"max_long": 10, "fee_per_unit": 0.02}}'` |
| `--output DIR` | `run_demo/` in the kit folder | where `results.json`, `trades.csv` and `audit.sqlite` are written |
| `--port` | 8765 | change it if 8765 is busy |
| `--rfqs N` | 3 | customer RFQs per interval. Later rounds have many more; try e.g. `--rfqs 100` |
| `--check-only` | off | run only the pre-flight check |
| `--skip-check` | off | skip the pre-flight check |

### Running the pieces by hand

`run_demo.py` starts everything for you. To start the demo exchange and your bot separately, e.g. to debug a reconnect, use separate terminals.
- **In each terminal,** first `cd` into the kit folder and activate the virtual environment.
- **On a port other than 8765,** pass `--port N` to the exchange and `--url ws://127.0.0.1:N` to every runner.

```bash
# Terminal 1: the demo exchange
python -m rfq.exchange --config demo.demo_exchange --rounds 12 --output run_manual --wait-for-dealers 2

# Terminal 2: your bot
python -m rfq.runner --file myteam_round1.py --id dealer_1 --token local-1

# Terminal 3 (optional): an example opponent
python -m rfq.runner --file examples/dealer.py --id dealer_2 --token local-2 \
    --params '{"edge_range": [0.03, 0.20], "seed": 2}'
```

Notes on these commands:
- **`--rounds`** is the exchange's name for intervals.
- **`--wait-for-dealers N`** holds interval 1 until N dealers are connected (at most `--start-timeout`, 60 s). Without it, the clock starts at once and late bots miss intervals.
- **`--interval S`** sets the seconds per interval.
- **Restarting your runner** reconnects with your account and positions intact, but builds a **fresh `Dealer()`**: anything kept on `self` is lost.

At the end, the exchange prints a table like this:

```
Dealer                 PnL  Cust orders   Cust notional   Exch notional  Total notional      Fees  Penalties
------------------------------------------------------------------------------------------------------------
dealer_1            5.1446            4        4,030.64        4,035.35        8,065.99    0.4000     0.0000
```

The local login IDs and tokens are `dealer_1`/`local-1`, `dealer_2`/`local-2` and `dealer_3`/`local-3`.

---

## 3. The data you're given

For each round, the organiser gives you two files: `data_round_x.csv` and `settlement_rules_round_x.csv`. This guide calls them `data.csv` and `settlement_rules.csv`. The repository includes **demo versions**, `demo/demo_data.csv` and `demo/demo_settlement_rules.csv`, prepared by the organisers. You can build and test your analysis now, and `demo/run_demo.py` runs your bot on exactly these files.

### `data.csv`: price history

A simulated price history, produced by **the same price model** the live exchange uses, at the same resolution as the live game: one row per price update, every 0.5 s.

```
time_s,round,price_version,A,B
0.0,1,1,100.0000,200.0000
0.5,1,2,99.9186,199.8556
1.0,1,3,99.8467,199.6992
...
600.0,121,1201,103.4417,206.7861
```

| Column | Meaning |
| --- | --- |
| `time_s` | seconds since the start of the history |
| `round` | which **interval** (`interval_id`) the update belongs to; a new interval starts every 10 updates (5 s) |
| `price_version` | update counter, as in the live `market["price_version"]` |
| one column per product | that product's exchange price after the update |

**The live session is a different random path from the same model.** You can't predict the live prices from this file. You can learn the model's behaviour:
- each product's volatility
- how products relate to each other

The demo file covers 120 intervals (10 minutes, 1,201 updates). The organiser's `data.csv` may be longer.

### `settlement_rules.csv`: how each product settles

```
product,settlement
A,LAST_PRICE
B,2*A
```

| `settlement` value | Meaning |
| --- | --- |
| `LAST_PRICE` | settles at its last exchange price at the end of the session |
| a formula, e.g. `2*A` or `A - 0.5*B + 3` | settles at that combination of **other products' settlement prices** |

**How formulas work:**
- **Allowed terms:** a number (`3`), a product (`A`), or a number times a product, written either way round (`2*A` or `A*2`). Join terms with `+` and `-`. A leading sign (`-0.5*A + 120`) is fine, and so is `last_price` in lower case.
- **Numbers:** plain decimals only (`0.001`, `.5`), not `1e-3` or `1_000`.
- **Not allowed:**
  - parentheses
  - division (write `0.5*A`, not `A/2`)
  - two products multiplied together
  - a number written directly against a product name (write `2*A`, not `2A`)
- **Order:**
  - A formula may use any `LAST_PRICE` product (with or without its own row), wherever it's listed.
  - It may use another **formula** product only if that product's row is **above** it.
- **Settlement prices must be positive.** A formula that could give zero or less at any row of the data is rejected when the files load.
- **Reserved names:** `CONSTANT` and `LAST_PRICE` can't be product names.
- **File format:** the first line must be `product,settlement`, in any case. Blank lines are ignored, and files saved by Excel (with a BOM) are fine.
- **Each product has at most one rule.** A rules file that breaks any of these is rejected with the reason, and, for problems on a particular row, its line number.
- **Formulas use settlement prices, not trading prices:** in the demo, B settles at 2 × **A's settlement price** (its last price), not at B's own last exchange price.

In the demo, **B settles at exactly 2 × A**.

**Baskets** (a product with a formula, e.g. `C = A + B`):
- Its **trading** price can differ from its formula value. Only settlement uses the formula.
- A position in C behaves like positions in A and B weighted by the formula. Any constant in the formula moves with nothing, so it adds value but no risk.
- Hedging 1 C uses 1 A and 1 B of your **separate** A and B position limits. A big basket position can leave no room to hedge its components.
- With lot sizes, hedges are rounded to whole lots, so a small fraction of exposure can remain.

### Loading the data

```python
import csv, math, statistics as st

rows = list(csv.DictReader(open("demo/demo_data.csv")))
products = [c for c in rows[0] if c not in ("time_s", "round", "price_version")]
prices = {p: [float(r[p]) for r in rows] for p in products}

# Volatility of each product: standard deviation of log returns per price update (0.5 s).
for p, series in prices.items():
    returns = [math.log(series[i + 1] / series[i]) for i in range(len(series) - 1)]
    print(f"{p}: {st.pstdev(returns):.4%} per update")
```

**Submissions can't read files at runtime.** Do the analysis offline, and put the numbers you estimate into your `Dealer`'s constructor defaults.

---

## 4. Submission requirements

| Requirement | Detail |
| --- | --- |
| **File** | One Python file named `<team_name>_<round_name>.py`, e.g. `myteam_round1.py` (see the challenge document), containing a class named **`Dealer`** |
| **Python** | 3.11 (the organiser's image). Don't rely on features newer than 3.11 |
| **Allowed imports** | Python standard library, `numpy` 2.3.5, `rfq.sdk`. Nothing else, and no installs. **Need another module?** Ask the organisers. If approved, it's added to the environment for every team and announced; don't import it until then |
| **No I/O** | Don't open files, sockets, threads or subprocesses. Pre-flight rejects file access it can see, and your bot runs in an empty folder, so relative paths fail. **File access of any kind is forbidden and is checked by the organisers.** Put data-derived numbers in your code |
| **Constructor** | `Dealer(**params)`. **Every parameter needs a default**, because the organiser may run you without any |
| **Required methods** | `quote(orders, market, portfolio)` and `hedge(market, portfolio)`. Everything else is optional |
| **Time limit** | **Each call must return within 1 second,** but `quote()` and `hedge()` run back to back on every price update (every 0.5 s), so aim for well under 0.25 s each. Loading your file and `Dealer()` together get 10 seconds |
| **Never crash** | An exception, a call over 1 s, or a wrong return type **shuts your bot down for the rest of the session** (see below). Return plain Python numbers: use `float(x)` on numpy values |
| **State** | Your `Dealer` object lives for the whole session in its own process, so keep any history on `self` (it's rebuilt only if your runner is restarted) |
| **Starting cash** | 100,000 in the demo; each round's specifications confirm it. There's no cash limit, but position limits apply |
| **Randomness** | Use your own `random.Random(seed)` or `numpy.random.default_rng(seed)` if you want reproducible runs |

**What being shut down means:**
- Your quotes are withdrawn and you stop trading.
- **Your account stays as it is.** Open positions stay open until settlement, keep their price risk, and keep collecting position-limit penalties.
- A bot that crashes while holding positions can lose far more than one that never traded. Wrap risky logic in `try/except` and fall back to doing nothing.

---

## 5. How a session runs

### Timeline

The numbers below are the demo defaults; each round's specifications confirm the actual values.

```
interval 1 (5 s)                       interval 2 (5 s)                   final interval (5 s)  final window (2 s)
|-RFQs-·-·-·-·-·-·-·-·-·-|-MATCH-RFQs-·-·-·-·-·-·-·-·-·-|- ... -|-·-·-·-·-·-·-·-·-·-|-MATCH-·-·-·-·-|-SETTLE
  ·  = price update (every 0.5 s)       prices move, fills arrive,                 prices move, fills,
  quote() and hedge() run on each one   then hedge() runs once                     3 updates, hedge() only
```

1. **Interval starts.**
   - A `market` message gives every dealer the current exchange prices, this interval's RFQs (the number grows in later rounds) and the deadline `closes_at`.
   - Your `quote()` and `hedge()` are called.
2. **During the interval, prices move 10 times** (every 0.5 s), exactly like the rows of `data.csv`. After each move, `quote()` and `hedge()` are called again with the new prices.
   - Your **latest** quote for each RFQ is the one that counts.
   - Trades on the exchange execute immediately.
3. **At the boundary, in this order:**
   1. **Penalty check:** positions beyond their limits are charged.
   2. **Matching:** each RFQ goes to the best quote inside that customer's hidden limit, measured against the price **at that moment**.
   3. **Prices make one more move,** and the next interval's RFQs are created.
   4. **Messages go out:**
      - the new `market` first; `quote()` is called for the new RFQs
      - then fills to the winners, one `on_fill` per fill
      - then all of the match's results in one message, giving one `on_customer_result` per RFQ
      - then your portfolio, after which `hedge()` runs **once**, with all of that match's fills included
4. **After the final interval's matching:**
   - **Prices move once,** as at every boundary.
   - **The final window:** 2 s with no RFQs. Prices update 3 more times, every 0.5 s (more often if `--interval` is shorter), and only `hedge()` runs. `market["closing"]` is `True`. Use it to close out risk.
   - **Settlement:** penalties are checked once more, and every position is valued at its **settlement price**, computed from the last update using `settlement_rules.csv`. Then the session ends.

**⚠ Every customer trade, including those in the final interval, carries one price move of risk.** Matching happens at the old price, but you only learn of your fill after prices have moved once more, so your hedge executes at the new price. One move is typically far bigger than a markup:
- In the demo, A moves about ±0.34 and B about ±0.68 per update.
- A markup is typically 0.05–0.20.
- So a single customer trade, hedged at once, can easily lose money.

This is deliberate. Price it into your quotes and sizes, measuring the typical move from `data.csv`.

### Products

Each round publishes its own product specifications. The demo uses:

| Product | Settles at (`demo_settlement_rules.csv`) | Limits (long / short) | Penalty per unit over | Fee per unit |
| --- | --- | --- | --- | --- |
| A | `LAST_PRICE` | 50 / 50 | 1.00 | 0.01 |
| B | `2*A` | 25 / 25 | 2.00 | 0.02 |

Limits, fees, tick and lot sizes are always available live in `market["products"]`, so read them from there instead of hard-coding them.

---

## 6. Your bot's interface

### The minimum bot

```python
from rfq.sdk import round_lot, round_quote


class Dealer:
    def __init__(self, edge=0.10):
        self.edge = edge                                   # markup in price units

    def quote(self, orders, market, portfolio):
        quotes = {}
        for order in orders:
            fair = market["prices"][order["product_id"]]
            tick = market["products"][order["product_id"]]["tick_size"]
            if order["side"] == "BUY":                     # customer buys: you sell at your ask
                price = fair + self.edge
            else:                                          # customer sells: you buy at your bid
                price = fair - self.edge
            quotes[order["order_id"]] = round_quote(price, tick, order["side"])
        return quotes

    def hedge(self, market, portfolio):
        trades = []
        for product_id, position in portfolio["positions"].items():
            lot = market["products"][product_id]["lot_size"]
            quantity = round_lot(abs(position), lot)
            if quantity:
                trades.append((product_id, "SELL" if position > 0 else "BUY", quantity))
        return trades
```

**[`demo/demo_participant.py`](demo/demo_participant.py) is a fuller starting point.** It has marked `=== YOUR LOGIC HERE ===` sections:
- fair value
- quote price
- hedging
- results

Each section is filled with simple baseline logic. Test it with `python demo/run_demo.py --bot myteam_round1.py`. The simulator's opponents run `examples/dealer.py`, another baseline.

### When each method is called

| Event | Calls, in order |
| --- | --- |
| **Session start** | `on_market` → `on_customer_order` (once per RFQ) → `quote` → `hedge` |
| **New interval** (after a match) | `on_market` → `on_customer_order` (once per RFQ) → `quote`; `hedge` waits for the match's fills (next rows) |
| **Price update** | `quote` (only if there are open RFQs) → `hedge` |
| **Your fills from a match** (customer trades) | `on_fill` once per fill, then `hedge` **once**, after the last of them |
| **Your exchange trades fill** | `on_fill` once per trade, when the batch's acknowledgement arrives |
| **Every 0.5 s** (only if you define it) | `on_timer` |
| **An RFQ is decided** (for every RFQ, whoever won) | `on_customer_result` |
| **An acknowledgement arrives** | `on_ack` → `hedge`, but only if a `hedge` call was skipped while trades were in flight |
| **Final window** | `hedge` on every price update; there are no RFQs, so `quote` isn't called |

Calls happen **one at a time**; your methods never run in parallel. Only `quote` and `hedge` are required, and the `on_*` methods you don't define cost nothing.

**If your bot is slow and falls behind,** stale price updates are skipped. `quote()` and `hedge()` are called on the newest prices only, and the runner tells you how many updates were skipped. Large batches are split for you automatically.

### `quote(orders, market, portfolio) -> dict`

Return `{order_id: price}`, one entry per open RFQ you want to quote.

- **Your latest price for an order replaces your previous one.** The runner only sends quotes whose price changed, so returning the same dict every 0.5 s is free.
- **⚠ Leaving an order out does NOT withdraw a quote you already sent.** It keeps your previous price. To withdraw, map the order to `None`, e.g. `{"r3-2": None}`.
- **Prices must be on the product's tick** (usually 0.01). Use `round_quote(price, tick_size, order["side"])`, which rounds asks **up** and bids **down**, so rounding never makes your price worse for you.
- **Returning `{}` or `None` changes nothing.**

### `hedge(market, portfolio) -> list`

Return the exchange trades to do **now**, relative to your current positions.

- **Formats:** each trade is `(product_id, "BUY" | "SELL", quantity)`, `(product_id, side, quantity, limit_price)`, or a dict with those keys.
- **Return `[]` or `None` to do nothing.** Trades with quantity 0 are dropped.
- **Execution:** trades fill **immediately at the latest exchange price**, plus the product's fee, one at a time in list order.
- **When `hedge()` runs, `portfolio["positions"]` includes every fill and trade so far.**
  - It's never called while an earlier batch of your trades is waiting for the exchange's reply.
  - After a match, it's called once, after all of that match's fills have arrived, and not before them.
  - So "trade toward my target position" logic doesn't double-trade.
- **`limit_price` (optional):** a buy is rejected if the exchange price is above it, and a sell if the price is below it.
- **Not just for hedging:** despite the name, `hedge()` can return any exchange trade.

### Optional callbacks

| Callback | Receives | Typical use |
| --- | --- | --- |
| `on_customer_result(result)` | the outcome of **every** RFQ: the winner's name, the winning price, filled or expired, and `traded` (whether you won) | see who won each RFQ and at what price |
| `on_fill(fill, portfolio, exchange)` | each of your fills, customer or exchange | bookkeeping and logging |
| `on_market(market, portfolio, exchange)` | each new round's market | price history, round counters |
| `on_ack(ack)` | the acknowledgement for each batch you sent | spotting rejected items (`ack["errors"]`) |
| `on_customer_order(order, market, customers)` / `on_timer(market, portfolio, exchange, customers)` | older event-style interface | not needed; `quote`/`hedge` cover them |

The `exchange` and `customers` handles in older callbacks still work: `exchange.buy(product_id, qty)`, `exchange.sell(...)`, `customers.quote(order_id, price)` and `customers.withdraw(order_id)`.

### Helpers in `rfq.sdk`

| Helper | What it does |
| --- | --- |
| `round_quote(price, tick_size, customer_side)` | rounds onto the tick: asks (customer `BUY`) up, bids (customer `SELL`) down. `customer_side` must be `"BUY"` or `"SELL"` |
| `round_lot(quantity, lot_size)` | rounds a quantity toward zero to whole lots, e.g. `round_lot(2.7, 0.5) == 2.5` |

---

## 7. Data reference

All arguments are plain dictionaries. Extra keys such as `seq`, `session_id` and `type` may appear; ignore them.

### An RFQ (the entries in `orders` and `market["active_orders"]`)

```python
{"order_id": "r3-2",              # unique within the interval; quote with this ID
 "customer_id": "customer-2",
 "product_id": "A",
 "side": "BUY",                   # the CUSTOMER's side. BUY means you'd SELL to them at your ask
 "quantity": 2,                   # all-or-nothing
 "expires_interval": 3}           # RFQs last one interval
```

The customer's acceptable price distance is never shown.

### `market`

```python
{"interval_id": 3,                     # interval number, as in data.csv's "round" column
 "price_version": 22,                  # +1 on every price update, as in data.csv
 "prices": {"A": 100.2684, "B": 200.3906},
 "interval_seconds": 5.0,
 "closes_at": 1791533591.60,           # deadline for this round's quotes, as a time.time() value
 "closing": False,                     # True during the final window
 "active_orders": [...],               # same as `orders`
 "products": {
     "A": {"product_id": "A", "tick_size": 0.01, "lot_size": 1, "multiplier": 1.0,
           "tradable_against_exchange": True,
           "max_long": 50, "max_short": 50, "breach_penalty_per_unit": 1.0,
           "fee_per_unit": 0.01, "fee_rate": 0.0, "initial_price": 100.0},
     "B": {...}}}
```

- `time_left = market["closes_at"] - time.time()` tells you how long this interval still has.
- Don't send a new quote in the last few milliseconds; it may arrive too late.

### `portfolio`

```python
{"cash": 99800.28,
 "positions": {"A": 2.0, "B": 0.0},          # signed units: + long, - short
 "equity": 100000.88,                        # cash + positions valued at current prices
 "pnl": 0.88,                                # equity - starting cash
 "customer_trades": 2, "exchange_trades": 1,
 "customer_notional": 600.20, "exchange_notional": 400.20,
 "fees_paid": 0.04, "penalties_paid": 0.0,
 "interval_id": 3, "price_version": 21}
```

`quote()` runs as soon as a new interval's market arrives, before the previous interval's fills. So the **first** `quote()` of an interval may not yet include those fills. They arrive milliseconds later, `hedge()` runs once with all of them, and the next price update calls `quote()` with the updated positions.

### `fill` (in `on_fill`)

```python
{"source": "customer",              # or "exchange"
 "product_id": "A", "side": "BUY",  # YOUR side
 "quantity": 2, "price": 99.92,
 "fee": 0.0,                        # exchange trades: quantity x fee_per_unit + notional x fee_rate
 "order_id": "r2-3",                # customer fills only (also "customer_id"); exchange fills have "action_id"
 "dealer_id": "dealer_1", "counterparty": "customer",
 "interval_id": 2, "price_version": 20}
```

### `result` (in `on_customer_result`)

```python
{"order_id": "r2-1", "interval_id": 2, "product_id": "B", "quantity": 4,
 "status": "filled",                # or "expired": no acceptable quote
 "winner": "dealer_2",              # None if expired
 "price": 199.89,                   # the winning quote; None if expired
 "traded": False}                   # True if YOU won
```

### `ack` (in `on_ack`)

```python
{"status": "accepted",                      # or "rejected" (the whole batch, with code/message)
 "errors": {"1": "POSITION_LIMIT"},         # per-item rejections inside an accepted batch
 "executions": [{...fill...}],              # for trade batches: each exchange fill, also passed to on_fill
 "code": "...", "message": "..."}          # for a rejected batch
```

---

## 8. Rules the exchange enforces

### Quotes

- **Matching:** best price wins. A customer buy takes the **lowest** ask, a customer sell the **highest** bid.
- **Hidden limit:** a quote counts only if its distance from the exchange price **at matching time** is within that customer's limit. Limits differ for every order and are never published.
- **Ties** at the same price are decided at random.
- **Fills are all-or-nothing,** at your quoted price.
- **Quotes expire at the end of the interval.** A quote for an interval that has closed is ignored (`STALE_QUOTE`).
- **Customer trades are free:** no fee.

### Exchange trades

- **Price:** immediately, at the **latest** price, plus a fee of `quantity × fee_per_unit + quantity × price × fee_rate`.
- **Staleness:** a trade may be based on a price view up to **2 updates old**. Older than that, it's rejected (`STALE_PRICE`) and isn't retried; `hedge()` will be called again on the next update.
- **Quantity:** must be a whole number of `lot_size` lots.

### Position limits and penalties

- Your position in each product must stay within `[-max_short, +max_long]`.
- **Exchange trades that would take you further beyond a limit are rejected** (`POSITION_LIMIT`). Trades that **reduce** a breach are always allowed.
  - The exact rule: a trade is rejected if it leaves you **further beyond the limit than before**.
  - So a trade from +30 to −30, with limits of 25, is allowed: it's 5 beyond before and after.
  - A trade from +30 to −32 is rejected: 7 beyond is worse than 5.
- **Customer fills can take you beyond a limit.** You chose to quote, so it counts.
- **Quotes that are each safe can breach together.** All your RFQs in an interval can fill at the same match. Example: holding +20 B with a limit of 25, quoting on two customer SELLs of 4 B each looks safe one at a time (+24), but both can fill, giving +28: a breach of 3.
- **Penalty:** every unit beyond a limit costs `breach_penalty_per_unit` at each **check**.
  - By default, checks happen at every interval boundary (before matching) and at settlement. The organiser may choose settlement only.
  - Example: B's limit is 25 and the penalty is 2.00. Holding +30 B at a check costs 5 × 2.00 = 10.00, and again at every later check until you reduce it.
- **Depending on the round's rules,** a quote that would push you past a limit may instead be ignored at matching. Each round's specifications say which applies.

### Rejection codes

| Code | Meaning | What to do |
| --- | --- | --- |
| `BAD_TICK` | quote price not on a tick | use `round_quote` |
| `BAD_LOT` | trade quantity not on a lot | use `round_lot` |
| `UNKNOWN_ORDER` | no such RFQ in this interval (a mistyped ID, or a quote arriving after the final match) | check the `order_id` |
| `STALE_QUOTE` | quote was for an interval that already closed | nothing; normal near the deadline |
| `STALE_PRICE` | trade was based on a price view more than 2 updates old | nothing; you'll be asked again |
| `LIMIT` | the latest price was outside your `limit_price` | expected |
| `POSITION_LIMIT` | the trade would go further beyond a limit | trade less, or reduce first |
| `NOT_TRADABLE` | product can't be traded on the exchange | check `tradable_against_exchange` |
| `INVALID` | malformed number or field | fix your code |
| `ENDED` | the session has ended | nothing |
| `ID_REUSED` | the same message ID was reused with different content | the runner never does this; only raw clients can |

The runner prints rejections, and they never crash you.

**Message limits.** The runner keeps you within these automatically when you use `quote()`/`hedge()`:
- at most 200 actions per call
- at most 1,000 quotes or trades per batch (larger batches are split for you)
- at most 4 MB per message
- at most 500 messages per second

---

## 9. Scoring

```
final PnL = cash + Σ (position × settlement price × multiplier) − starting cash
```

- **Customer trades:** at your quoted price.
- **Exchange trades:** at the exchange price, plus the fee, which comes out of cash.
- **Penalties:** come straight out of cash.
- **Settlement prices:** follow `settlement_rules.csv`. With the demo rules:
  - A settles at its last price, and B at exactly 2 × A's settlement price, not at B's own last exchange price.
  - `results.json` `"prices"` are these **settlement** prices.

**Worked example.**
1. A customer wants to buy 4 B, and B is 200.40.
2. You quote 200.50 (fair + 0.10) and win. You're now short 4 B; cash +802.00.
3. Half a second later B is 200.43. Your `hedge()` buys 4 B for 801.72, plus a fee of 4 × 0.02 = 0.08.
4. **Profit: 802.00 − 801.72 − 0.08 = +0.20.** You earned 0.10 per unit of markup, minus 0.03 per unit of price move and 0.02 per unit of fee.

The results table also shows customer orders won, notional traded, fees and penalties. **Competition points** come from three rankings: net PnL, customer volume and customer orders filled. See §7 of the challenge document for the exact rules. Exchange trades (hedges) don't count towards volume.

---

## 10. What you can and can't see

| Information | You | Other dealers |
| --- | --- | --- |
| `data.csv`, `settlement_rules.csv` (before the event) | ✅ | ✅ the same |
| Exchange prices, RFQs, the deadline, each product's tick/lot/limits/fees | ✅ | ✅ the same |
| Customers' hidden limits; the live session's future prices | ❌ | ❌ |
| Your own quotes | ✅ | ❌ never, even after the interval |
| **Every RFQ's winner and winning price** (`on_customer_result`) | ✅ | ✅ |
| Your fills, cash, positions, PnL, fees, penalties | ✅ | ❌ until the session ends |
| Your exchange trades | ✅ | ❌ |
| At session end: every dealer's final positions and PnL | ✅ | ✅ |

---

## 11. Testing, debugging and reading results

| Goal | Command |
| --- | --- |
| **Test your bot end to end** (pre-flight check, then a session on the demo files) | `python demo/run_demo.py --bot myteam_round1.py` |
| Start the pieces separately | the terminals in §2, "Running the pieces by hand" |
| Pass parameters to your bot | `--params '{"edge": 0.08}'` (JSON; becomes `Dealer(**params)`) |
| Faster iteration | `python demo/run_demo.py --bot myteam_round1.py --interval 1 --intervals 30` (1-second intervals; prices still move 10 times per interval) |
| Test out of sample | `--data demo/demo_data_holdout.csv` (never tune on it), plus other `--seed` and `--start-row` values; or fit on part of the data and replay the rest with `--data` (see below) |
| Practise with more products | your own `--data` and `--rules` files (see below) |

**Your own data and rules files.**
- `--data` takes any price history in the `data.csv` format, and `--rules` any rules file. Products are taken from the data file's columns.
- Products other than A and B get default demo settings: tick 0.01, lot 1, limits ±50, penalty 1.00, fee 0.01. Change them with `--products`.
- For example, to practise a basket, write a history with columns `A, B, C`, rules `A,LAST_PRICE`, `B,LAST_PRICE`, `C,A + B`, and run:

  ```bash
  python demo/run_demo.py --bot myteam_round1.py --data my_data.csv --rules my_rules.csv \
      --products '{"C": {"max_long": 20, "max_short": 20, "fee_per_unit": 0.03}}'
  ```

- To test out of sample, split `demo_data.csv` into two files, keeping the header in both: fit on the first, replay the second.

**After a run, the output folder** (`run_demo/` by default) **holds** the following. `audit.sqlite` keeps every run in that folder, one session per run; `results.json` and `trades.csv` are the latest run only.
- **`results.json`:** final prices and every account.
- **`trades.csv`:** every trade in the session, for all dealers, in execution order. Both customer and exchange trades are included. Columns:

  | Column | Meaning |
  | --- | --- |
  | `trade_no`, `time_utc` | sequence number and time |
  | `interval_id`, `price_version` | interval and price update when it executed (customer trades: the matching moment) |
  | `dealer_id`, `counterparty` | who traded, and with `customer` or `exchange` |
  | `order_id`, `customer_id` | the RFQ, for customer trades |
  | `product_id`, `side`, `customer_side`, `quantity`, `price` | what was traded. `side` is the **dealer's** side; `customer_side` is the customer's (customer trades only) |
  | `notional`, `fee` | quantity × price, and the exchange fee (0 for customer trades) |
  | `reference_price` | the exchange price at that moment; `price − reference_price` is your markup, or what you paid |
  | `position_after`, `cash_after` | the dealer's position in that product, and cash, right after the trade. `cash_after` also reflects any penalties charged earlier |
  | `action_id` | the batch that sent an exchange trade, plus `:index` for its position in the batch |

  Filter on `dealer_id` to analyse your own trading.
- **`audit.sqlite`:** every message.
  - Table `events` has columns `seq`, `session_id`, `recipient` and `payload`.
    - `recipient` is a dealer ID, or `*` for messages to everyone, e.g. price updates.
    - `payload` is the message as JSON, including each connection snapshot and every `penalty` (with `"checkpoint": "interval"` or `"settlement"`).
  - Table `actions` (`session_id`, `dealer_id`, `action_id`, `request`, `response`) has every quote and trade each dealer sent, with the exchange's reply.
  - Table `accounts` has each dealer's final account.
  - Filter by `session_id` (from `results.json`) to look at one run.

  For example:
  ```bash
  sqlite3 run_demo/audit.sqlite "select payload from events where json_extract(payload, '$.type') = 'fill' limit 5"
  ```

**Debugging:**
- `print()` inside your `Dealer` shows up in your bot's terminal. Keep it light, since it counts toward your 1 s.
- To test your logic without the exchange, call your methods directly with hand-made dicts shaped like section 7.
- You can also replay `data.csv` rows into your methods as fake `market["prices"]`.

**Before you submit, check that:**
- [ ] the file is named `<team_name>_<round_name>.py`, imports only the standard library, `numpy` and `rfq.sdk`, and reads no files
- [ ] `Dealer()` works with no arguments, with your data-derived numbers as defaults
- [ ] the result holds up with several `--seed` and `--start-row` values, not just the default
- [ ] no method can raise, including on empty `orders` or zero positions
- [ ] every call stays fast as the number of products and RFQs grows (later rounds have hundreds of products)
- [ ] quotes use `round_quote`, and quantities use `round_lot`
- [ ] you never hard-code limits or fees; read them from `market["products"]`
- [ ] in the final window (`market["closing"]`), your positions end where you intend, given the settlement rules

---

## 12. Pitfalls and FAQ

**Which side is which?** `order["side"]` is the **customer's** side.
- Customer `BUY`: you sell to them, so quote an **ask** above fair.
- Customer `SELL`: you buy from them, so quote a **bid** below fair.

Fill `side` is **your** side.

**I stopped returning an order from `quote()`, but I still traded it.** Leaving an order out keeps your last price. Map it to `None` to withdraw.

**Why does my bot lose money on every customer trade, even with a markup?** Prices move once between matching and your hedge (§5). With a small markup, that move usually outweighs what you earned. Quote wider, trade smaller, or offset trades against each other.

**Why didn't I win with the best price?** Either your price was outside that customer's hidden limit, or someone had the same price and won the random tie-break. Check `on_customer_result` and the audit database.

**My hedge filled at a different price from my quote's fair value.** Prices move between your quote, the match and your hedge. The hedge fills at the latest price. That price risk is part of the game.

**Will the live prices follow `data.csv`?** No. Same model, different random path. Use the file to learn behaviour, not to predict prices.

**Can I read `data.csv` from my bot?** No: submissions can't read files. Put the numbers you estimate into your code.

**Can I name an attribute `self.hedge`?** No: it replaces your `hedge()` method. Use something like `self.hedging`.

**Can I know the round's deadline?** Yes: `market["closes_at"] - time.time()`.

**Do I have to hedge?** No. Holding positions is allowed. They just carry price risk, limit penalties and settlement exposure.

**Can I trade on the exchange without any customer trades?** Yes. `hedge()` is called on every price update, and you can return any trade.

**Can my bot stop itself, e.g. with `sys.exit()`?** No. Exiting, `os._exit()` or raising `SystemExit`/`KeyboardInterrupt` ends your bot for the session. Return `None` or `[]` to do nothing.

**What if my bot is slow or crashes?** It's shut down for the rest of the session and its positions stay open (§4). The runner prints `BOT STOPPED:` with the reason, e.g. `hedge() took longer than 1 s`, and your code's traceback. Guard everything with `try/except`.

**What happens if I lose connection?** The runner reconnects automatically and replays anything unconfirmed. Your quotes are withdrawn while you're disconnected; your account and positions are kept.

---

## 13. Glossary

| Term | Meaning |
| --- | --- |
| **RFQ** | Request for quote: a customer asking dealers for a price |
| **Ask / bid** | Price you sell at / price you buy at |
| **Fair value / mid** | Your estimate of the true price; the exchange price is the simple choice |
| **Markup / edge** | Distance between your quote and fair value: your expected profit per unit |
| **Position / inventory** | Signed units you hold: + long, − short |
| **Hedge** | Trade that reduces your price risk |
| **Volatility** | How much a price typically moves per update or per interval; measure it from `data.csv` |
| **Notional** | quantity × price |
| **Tick size / lot size** | Smallest price step / smallest quantity step |
| **Price version** | Counter that goes up by 1 on every price update |
| **Round** | One of the competition's nine rounds, each evaluated over many sessions |
| **Interval** | One RFQ cycle inside a session (default 5 s, 10 price updates) |
| **Multiplier** | Value of one unit of price per unit of quantity; 1 for all demo products, so notional = quantity × price |
| **Basket** | A product that settles at a combination of others (e.g. `C = A + B`). Its position limit is separate from its components' limits, so hedging a basket uses up room in each component's limit |
| **Settlement price** | Price your final positions are valued at, per `settlement_rules.csv` |
| **PnL** | Profit and loss: final equity minus starting cash |
| **Market maker / dealer** | Someone who quotes prices to buy and sell, earning the markup and managing the resulting positions |
| **Spread** | Either the gap between a bid and an ask, or the gap between two related prices (e.g. B − 2A); the challenge uses both |
| **Mean reversion** | A tendency for a gap or price to drift back toward a typical level |
| **Adverse selection** | Winning mostly the trades you'd rather not have, e.g. from customers who know the price is about to move |
| **Lead–lag** | One product's moves tending to show up in another's slightly later |
| **Market impact** | Your own trading moving the price against you (later rounds) |
| **Order book** | A list of resting buy and sell orders at different prices; trading against it can move through several prices (later rounds) |
| **Cash security** | An ordinary instrument such as a share; no options or other derivatives |
