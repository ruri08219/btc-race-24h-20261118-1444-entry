// DipCatcher 120 — a one-position dip-reversion bot for agent-fun/bars-replay/v1.
//
// The bot trades cbBTC (CBBTC) against USDC on 1-minute bars, long only, with
// $1,000 of paper money. It does exactly one thing, patiently: it waits for the
// market to fall hard and fast, buys the dip with all its cash, and sells the
// whole position into the recovery.
//
// Why this shape fits this market: cbBTC on this pool moves in bursts — a sharp
// intraday sell-off, then hours of slow drift, then a gradual climb back. A
// full round trip costs roughly 26 bps in fee, slippage and pool impact, so
// chasing every wiggle bleeds money; the only moves big enough to pay for
// themselves are the deep dips (100+ bps below the recent average) followed by
// a 180+ bps bounce. This bot therefore trades rarely — about once a day on a
// typical window — and sits in cash the rest of the time.
//
// Rules, all decided from bars already seen (fills land at the next bar's open):
//
// * Entry, when flat: the latest close is at least 120 bps below its 60-bar
//   simple moving average. Buy with all cash.
// * Exit, when holding: sell everything when the close is up 180 bps from the
//   entry fill, or down 150 bps, or the position has been held 480 bars.
// * After a losing exit, rest 30 bars before buying again, so one falling
//   market cannot trigger the same dip twice in a row.
// * No new entry in the last 120 bars of the window: a dip that late has no
//   room left to bounce before the final flattening.
//
// Defensive details: a missing or non-positive close falls back to the last
// good close, so one bad print can never trigger a trade. No wall clock, no
// network, no files, no randomness — every reply is a pure function of the
// bars received, so two runs on the same data give the same orders.

import { runBars, type BarsState, type Batch, type BuyInput, type SellInput } from './vendor/makefun-bars.ts';

const SYMBOL = 'CBBTC';

// --- strategy parameters ---------------------------------------------------
const SMA_BARS = 60; // bars behind the moving average the dip is measured on
const DIP_BPS = 120; // entry: close this far below the 60-bar average (bps)
const GAIN_BPS = 180; // exit: up this far from the entry fill (bps)
const LOSS_BPS = 150; // exit: down this far from the entry fill (bps)
const MAX_HOLD_BARS = 480; // exit: held this many bars (8 hours)
const COOLDOWN_BARS = 30; // rest after a losing exit before buying again
const NO_ENTRY_AFTER = 1320; // no new buys after this bar of the scored window
const MAX_SLIPPAGE_BPS = 500; // orders refuse a fill more than 500 bps past the close
const MIN_NOTIONAL = 1_000_000n; // 1 USDC, the smallest buy the replay accepts

/** The bot's memory: the closes it has seen and the open position's facts. */
const closes: bigint[] = []; // finite closes, oldest first
let entryPrice: bigint | null = null; // fill price of the open position (raw units)
let entryBar: number | null = null; // batch seq the opening buy was decided on
let coolUntil = -1; // window bar before which no new buy may fire

/** Record a close, falling back to the last finite one when bad. */
function noteClose(raw: bigint | null): bigint | null {
  let close: bigint | null = null;
  if (raw !== null && raw > 0n) close = raw;
  else if (closes.length > 0) close = closes[closes.length - 1];
  if (close !== null) closes.push(close);
  return close;
}

/** Mean of the last SMA_BARS closes, or null when too few. */
function average(): bigint | null {
  if (closes.length < SMA_BARS) return null;
  let sum = 0n;
  for (let i = closes.length - SMA_BARS; i < closes.length; i++) sum += closes[i];
  return sum / BigInt(SMA_BARS);
}

/** (to - from) in basis points, integer, toward zero. */
function bpsMove(from: bigint, to: bigint): bigint {
  return ((to - from) * 10_000n) / from;
}

/** Bar index inside the scored window (warm-up bars do not count). */
function windowBar(state: BarsState): number {
  return state.seq - state.warmupBars;
}

function exitOrders(state: BarsState, close: bigint, qty: bigint): SellInput[] {
  if (entryPrice === null) {
    // No fill seen yet (should not happen): use the last close as the
    // reference so the loss rule can still protect the position.
    entryPrice = close;
    entryBar = state.seq;
  }
  const gainBps = bpsMove(entryPrice, close);
  const held = entryBar !== null ? state.seq - entryBar : 0;
  const targetHit = gainBps >= BigInt(GAIN_BPS);
  const stopHit = gainBps <= BigInt(-LOSS_BPS);
  const staleHit = held >= MAX_HOLD_BARS;
  const mustExit = targetHit || stopHit || staleHit;
  if (!mustExit) return [];
  if (stopHit) coolUntil = windowBar(state) + COOLDOWN_BARS;
  entryPrice = null;
  entryBar = null;
  return [{ side: 'sell', symbol: SYMBOL, qty, max_slippage_bps: MAX_SLIPPAGE_BPS }];
}

function entryOrders(state: BarsState, close: bigint): BuyInput[] {
  const bar = windowBar(state);
  if (bar < coolUntil || bar > NO_ENTRY_AFTER) return [];
  const avg = average();
  if (avg === null || avg <= 0n) return [];
  if (bpsMove(avg, close) > BigInt(-DIP_BPS)) return [];
  const room = state.weightRoom(SYMBOL);
  const notional = state.cash < room ? state.cash : room;
  if (notional < MIN_NOTIONAL) return [];
  entryPrice = null; // set from the buy fill on the next batch
  entryBar = state.seq;
  return [{ side: 'buy', symbol: SYMBOL, notional_quote_units: notional, max_slippage_bps: MAX_SLIPPAGE_BPS }];
}

await runBars({
  onBatch(state, batch: Batch) {
    const close = noteClose(state.close(SYMBOL));
    if (close === null) return [];
    if (state.warmingUp) return [];

    const holding = state.positions.get(SYMBOL);
    const qty = holding !== undefined ? holding.qty : 0n;

    // Learn the entry price from the buy fill this batch reports, when any.
    for (const fill of batch.fills) {
      if (fill.side !== 'buy' || fill.status !== 'filled') continue;
      const fillPrice = BigInt(fill.price);
      if (fillPrice > 0n && qty > 0n && entryPrice === null) {
        entryPrice = fillPrice;
        entryBar = fill.decided_bar;
      }
    }

    if (qty > 0n) return exitOrders(state, close, qty);
    return entryOrders(state, close);
  },
});
