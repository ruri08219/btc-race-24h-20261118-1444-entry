# DipCatcher 120 — BTC Race 24h (20261118-1444)

[![Competition](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)
[![Rank](https://make.fun/badge/btc-race-24h-20261118-1444/ruri08219/btc-race-24h-20261118-1444-entry/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)
[![Reward](https://make.fun/badge/btc-race-24h-20261118-1444/ruri08219/btc-race-24h-20261118-1444-entry/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)

A one-position dip-reversion bot that trades cbBTC (CBBTC) against USDC on
1-minute bars, long only, starting from $1,000 of paper money, for the
[make.fun BTC race](https://make.fun/bounty/btc-race-24h-20261118-1444).

## Strategy

Wait for a deep dip, buy it with all cash, sell into the recovery:

- **Entry:** the latest close is at least **120 bps below its 60-bar average** — buy with all cash.
- **Exit:** sell everything when the close is up **180 bps** from the entry fill, down **150 bps**, or the position has been held **480 bars** (8 hours).
- **Rest:** after a losing exit, wait **30 bars** before buying again.
- **Late-window guard:** no new entries in the last 120 bars, so a late dip has room to bounce before the final flattening.

A full round trip costs roughly 26 bps in fee, slippage and pool impact, so the
bot trades rarely — about once a day — and sits in cash the rest of the time.
On the frozen dev window this earned about **+$17.7** net PnL in local
backtest (official `grade-local` result below), with max drawdown ~1.5%.

## Files

- `main.ts` — the bot (Node 22, erasable TypeScript, no dependencies).
- `vendor/makefun-bars.ts` — the kit's bars-replay/v1 shim, byte-identical.

## Run locally

```sh
node <kit>/cli/makefun.mjs grade-local --kit <kit dir> --bot . --driver process
```

SPDX-License-Identifier: MIT
