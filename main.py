"""DipCatcher 120 — a one-position dip-reversion bot for agent-fun/bars-replay/v1.

The bot trades cbBTC (CBBTC) against USDC on 1-minute bars, long only, with
$1,000 of paper money. It does exactly one thing, patiently: it waits for the
market to fall hard and fast, buys the dip with all its cash, and sells the
whole position into the recovery.

Why this shape fits this market: cbBTC on this pool moves in bursts — a sharp
intraday sell-off, then hours of slow drift, then a gradual climb back. A
full round trip costs roughly 26 bps in fee, slippage and pool impact, so
chasing every wiggle bleeds money; the only moves big enough to pay for
themselves are the deep dips (100+ bps below the recent average) followed by
a 180+ bps bounce. This bot therefore trades rarely — about once a day on a
typical window — and sits in cash the rest of the time.

Rules, all decided from bars already seen (fills land at the next bar's open):

* Entry, when flat: the latest close is at least 120 bps below its 60-bar
  simple moving average. Buy with all cash.
* Exit, when holding: sell everything when the close is up 180 bps from the
  entry fill, or down 150 bps, or the position has been held 480 bars.
* After a losing exit, rest 30 bars before buying again, so one falling
  market cannot trigger the same dip twice in a row.
* No new entry in the last 120 bars of the window: a dip that late has no
  room left to bounce before the final flattening.

Defensive details: a missing or non-positive close falls back to the last
good close, so one bad print can never trigger a trade. No wall clock, no
network, no files, no randomness — every reply is a pure function of the
bars received, so two runs on the same data give the same orders.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"

# --- strategy parameters -----------------------------------------------------
SMA_BARS = 60            # bars behind the moving average the dip is measured on
DIP_BPS = 120            # entry: close this far below the 60-bar average (bps)
GAIN_BPS = 180           # exit: up this far from the entry fill (bps)
LOSS_BPS = 150           # exit: down this far from the entry fill (bps)
MAX_HOLD_BARS = 480      # exit: held this many bars (8 hours)
COOLDOWN_BARS = 30       # rest after a losing exit before buying again
NO_ENTRY_AFTER = 1320    # no new buys after this bar of the scored window
MAX_SLIPPAGE_BPS = 500   # orders refuse a fill more than 500 bps past the close
MIN_NOTIONAL = 1_000_000  # 1 USDC, the smallest buy the replay accepts


class DipCatcher:
    """The bot's memory: the closes it has seen and the open position's facts."""

    def __init__(self):
        self.closes = []         # finite closes, oldest first
        self.entry_price = None  # fill price of the open position (raw units)
        self.entry_bar = None    # window bar the opening buy was decided on
        self.cool_until = -1     # window bar before which no new buy may fire

    def note_close(self, raw_close):
        """Record a close, falling back to the last finite one when bad."""
        close = None
        try:
            value = int(raw_close)
        except (TypeError, ValueError):
            value = -1
        if value > 0:
            close = value
        elif self.closes:
            close = self.closes[-1]
        if close is not None:
            self.closes.append(close)
        return close

    def average(self):
        """Mean of the last SMA_BARS closes, or None when too few."""
        if len(self.closes) < SMA_BARS:
            return None
        return sum(self.closes[-SMA_BARS:]) // SMA_BARS


BOT = DipCatcher()


def _bps_move(from_price, to_price):
    """(to_price - from_price) in basis points, integer, toward zero."""
    return (to_price - from_price) * 10_000 // from_price


def _window_bar(state):
    """Bar index inside the scored window (warm-up bars do not count)."""
    warmup = getattr(state, "warmup_bars", 0) or 0
    return state.seq - warmup


def on_batch(state, batch):
    """Decide this batch's orders from the bar it carries."""
    close = state.close(SYMBOL)
    if close is None:
        return []
    close = BOT.note_close(close)
    if close is None:
        return []
    if state.warming_up:
        return []

    holding = state.positions.get(SYMBOL)
    qty = holding.qty if holding is not None else 0

    # Learn the entry price from the buy fill this batch reports, when any.
    for fill in batch.get("fills", []):
        if fill.get("side") != "buy" or fill.get("status") != "filled":
            continue
        try:
            fill_price = int(fill["price"])
        except (KeyError, TypeError, ValueError):
            continue
        if fill_price > 0 and qty > 0 and BOT.entry_price is None:
            BOT.entry_price = fill_price
            try:
                BOT.entry_bar = int(fill["seq"])
            except (KeyError, TypeError, ValueError):
                BOT.entry_bar = state.seq

    if qty > 0:
        return _exit_orders(state, close, qty)
    return _entry_orders(state, close)


def _exit_orders(state, close, qty):
    """Sell the whole position when any exit rule fires, else hold."""
    if BOT.entry_price is None:
        # No fill seen yet (should not happen): use the last close as the
        # reference so the loss rule can still protect the position.
        BOT.entry_price = close
        BOT.entry_bar = state.seq
    gain_bps = _bps_move(BOT.entry_price, close)
    held = state.seq - BOT.entry_bar if BOT.entry_bar is not None else 0
    must_exit = (
        gain_bps >= GAIN_BPS
        or gain_bps <= -LOSS_BPS
        or held >= MAX_HOLD_BARS
    )
    if not must_exit:
        return []
    if gain_bps <= -LOSS_BPS:
        BOT.cool_until = _window_bar(state) + COOLDOWN_BARS
    BOT.entry_price = None
    BOT.entry_bar = None
    return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
             "max_slippage_bps": MAX_SLIPPAGE_BPS}]


def _entry_orders(state, close):
    """Buy with all cash on a deep dip below the 60-bar average."""
    bar = _window_bar(state)
    if bar < BOT.cool_until or bar > NO_ENTRY_AFTER:
        return []
    average = BOT.average()
    if average is None or average <= 0:
        return []
    if _bps_move(average, close) > -DIP_BPS:
        return []
    notional = min(state.cash, state.weight_room(SYMBOL))
    if notional < MIN_NOTIONAL:
        return []
    BOT.entry_price = None  # set from the buy fill on the next batch
    BOT.entry_bar = state.seq
    return [{"side": "buy", "symbol": SYMBOL,
             "notional_quote_units": notional,
             "max_slippage_bps": MAX_SLIPPAGE_BPS}]


makefun_bars.run(on_batch)
