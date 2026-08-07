# Write-to-Earn Optimization Edition

This build optimizes for the legitimate Write-to-Earn funnel rather than raw post count:

`market interest -> feed stop -> useful read -> cashtag click -> qualified trade`

It does **not** attempt to game Binance's private recommendation system and does not use fake engagement, guaranteed-profit language, or forced trade calls.

## What changed

### 1. W2E market score
`monetization.py` adds a market-level score built from public/observable proxies:

- 24h USDT turnover (liquidity)
- 24h trade count (active traders)
- current trending rank
- fresh 15m attention and relative volume
- movement strength without over-rewarding extreme pumps
- trade-plan actionability / R:R
- penalty for overextended late entries

The selector now weights **technical validity + fresh attention + W2E market score**. A technically pretty setup on an inactive coin can lose to a slightly less perfect setup on a market people are actually trading.

### 2. Conversion-intent score for every draft
Every generated draft is separately scored for:

- clickable `$CASHTAG` appearing naturally and early
- one clear decision condition (`if`, confirmation, level, invalidation)
- human decision context (`I wait`, `I would not chase`, etc.)
- anti-spam / trust signals

A post can be technically correct and readable but still be rejected if it is weak for the click-to-trade journey.

### 3. Revenue-first candidate ranking
The final post selector now combines:

- factual/technical quality
- feed appeal
- W2E conversion intent
- novelty / anti-repetition
- live-market context

### 4. Quality over cadence
The external cron may still trigger every 20 minutes, but the bot is allowed to skip a cycle when there is no sufficiently monetizable market candidate. This is intentional: low-quality/spam-like posts are poor for both distribution and W2E eligibility.

## New environment variables

- `MIN_W2E_MARKET_SCORE=56`
- `MIN_CONVERSION_INTENT=72`

Raise them to publish less often but more selectively. Lower them if the bot skips too many cycles.

## Important W2E mechanics

As of the 2026 Binance program description, a creator earns when an eligible reader clicks a coin cashtag or trading widget in eligible content and completes a qualified trade directly afterward. This build therefore prioritizes natural cashtag placement and useful trading context, but it cannot guarantee views, clicks, trades, ranking, or revenue.
