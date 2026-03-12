# Strategy Specification

## Strategy Identity

The V1 strategy is a deterministic long-only spot allocation system for Kraken.
It is rule-only.

The strategy uses point-in-time market-structure features, a BTC-led regime model, bounded portfolio construction, and layered drawdown-aware risk scaling.
There is no ML ranking, inference, promotion, or prediction layer in V1.

## Design Principles

- Trade only the fixed V1 universe.
- Hold cash in USD when not allocated.
- Use deterministic rules for admissibility, ranking, scaling, and exits.
- Optimize for aggressive after-fee return rather than ultra-low drawdown.
- Bias toward holding supported positions through ordinary crypto volatility.
- Avoid naive forced selling purely because a position is below entry.
- Allow the strategy to de-risk progressively when market structure weakens.

## Strategy Architecture

The strategy is a single deterministic rule shell.
It provides:

- universe enforcement
- signal windows and point-in-time feature derivation
- BTC-led market regime classification
- position-count and concentration limits
- cash-allocation policy
- layered portfolio drawdown scaling
- emergency freeze conditions
- hard trade vetoes when data or execution integrity is compromised

## Data Frequency and Decision Rhythm

The strategy operates primarily on end-of-day data and makes portfolio decisions at a fixed daily decision point.
Intraday monitoring exists for execution and freeze handling, not for high-frequency trading.

### Decision cadence

- Primary strategy evaluation: once per day
- Portfolio rebalance consideration: once per day
- Execution monitoring: continuous while the bot is running

The exact clock times may be adjusted during implementation, but they must remain fixed and consistent across backtest, simulate, and live modes.

## Universe and Eligibility

The strategy only considers these assets:

- BTC
- ETH
- BNB
- XRP
- SOL
- ADA
- DOGE
- TRX
- AVAX
- LINK

Eligibility for a trading decision requires:

- the asset is tradable on Kraken for the configured USD pair
- canonical Kraken data is present and passes integrity checks
- the latest point-in-time feature set is complete
- the asset is not blocked by exchange, runtime, or data-quality safeguards

## Deterministic Feature Set

The feature layer must include, at minimum:

- multi-horizon price momentum
- long-horizon trend state
- realized volatility
- relative strength versus the tracked universe
- breadth across the tracked universe
- BTC-led market regime features
- volume and liquidity sanity checks where available
- source-confidence ratios for Kraken versus fallback inputs

These features must be computed entirely from the canonical Kraken dataset for strategy decisions.
Supplementary exchange data may inform source confidence and validation but must not replace Kraken values in the primary signal set.

Derived datasets are feature-only.
They do not include forward labels, model targets, or prediction columns.

## Research Defaults

The implementation uses these daily research defaults:

- primary feature interval: 1 day
- default full-universe research and backtest dataset track: `dynamic_universe_kraken_only`
- default runtime preset: `live_default`
- default shell layers: regime filter on, entry filter on, volatility veto off, gradual reduction off
- momentum windows: 7, 30, and 90 trading days
- trend-gap windows: 50 and 200 trading days
- realized-volatility windows: 20 and 60 trading days
- relative-strength window: 30 trading days
- breadth windows: 30 trading days
- liquidity window: 20 trading days
- source-confidence window: 30 trading days

The BTC-led regime classification is:

- `frozen` when recent BTC source confidence is below 80% or required BTC regime inputs are incomplete
- `constructive` when BTC momentum and BTC trend gap are positive and tracked-universe breadth is at least 60%
- `defensive` when BTC momentum is below -5%, BTC trend gap is below -3%, or tracked-universe breadth is at most 35%
- `neutral` otherwise

## Portfolio Construction

### Asset count

- Maximum concurrent holdings in V1: 10
- The strategy may hold fewer than 10 assets when evidence is weak

### Weighting approach

Portfolio weights are generated through a bounded rule-based scoring process that combines:

- rule-based eligibility
- rule-based regime filter
- rule-based trend and momentum strength
- breadth support
- relative strength
- volatility penalty
- concentration limits
- drawdown-aware exposure scaling

The final weights must remain capped for concentration control.

## Runtime Presets

The implementation keeps two named runtime presets:

- `live_default`: the hardened preset intended for simulate and live mode by default
- `max_profit`: the more aggressive preset used to inspect the upside limit

### `live_default`

- regime filter: on
- entry filter: on
- volatility veto: off
- gradual reduction: off
- max positions: 3
- max asset weight: 35%
- rebalance threshold: 5%
- neutral exposure: 78%
- defensive exposure: 45%
- entry momentum floor: 0.0
- entry trend-gap floor: 0.0
- hold momentum floor: -3%
- hold trend-gap floor: -3%
- max realized volatility: 30%
- reduction volatility threshold: 16%
- held-asset score bonus: 2 points
- risk-state multipliers: 96%, 78%, 32%

### `max_profit`

- regime filter: on
- entry filter: on
- volatility veto: off
- gradual reduction: off
- max positions: 3
- max asset weight: 35%
- rebalance threshold: 5%
- neutral exposure: 85%
- defensive exposure: 55%
- entry momentum floor: -2%
- entry trend-gap floor: -1%
- hold momentum floor: -8%
- hold trend-gap floor: -6%
- max realized volatility: 45%
- reduction volatility threshold: 22%
- held-asset score bonus: 3 points
- risk-state multipliers: 100%, 85%, 40%

## Cash Behavior

- The bot may hold partial or full USD cash when the strategy is insufficiently constructive.
- Going to cash is permitted and expected when market-wide evidence is poor.
- Cash is a defensive state, not an alpha source.

## Entry Logic

An asset becomes a candidate for purchase or increased weight when:

- the rule shell marks the asset as eligible
- the market regime is not blocked
- source confidence and liquidity checks pass
- trend and momentum clear the configured floors
- the asset ranks high enough on the deterministic score to enter the bounded portfolio

When the regime is defensive, the rule shell may still admit top-ranked entries if long-horizon structure, relative strength, liquidity, and source confidence remain acceptable.

## Exit and Sell Logic

The sell policy is intentionally not a standard tight-stop momentum approach.
It is designed to tolerate normal crypto volatility while still reacting to material deterioration.

### Sell principles

- Do not exit solely because unrealized PnL is negative.
- Do not use a fixed percentage stop-loss from entry as the primary decision rule.
- Prefer rule-based reductions or lower exposure before routine full liquidation.
- Allow full exits when market structure or integrity conditions become unacceptable.

### Mandatory full exit conditions

The system must fully exit a position when any of the following are true:

- the asset becomes non-tradable or unsupported on Kraken
- data integrity is insufficient to continue holding responsibly
- the runtime enters a system freeze state
- source confidence fails
- liquidity validity fails
- a severe market-structure breakdown occurs

### Reduction conditions

The system may reduce but not necessarily fully exit when:

- the regime is defensive
- hold-support conditions fail without a hard exit condition
- short momentum weakens materially
- relative strength weakens materially
- breadth support deteriorates

When gradual reduction is enabled for research, reductions must remain above a practical floor so positions are not repeatedly halved into meaningless dust weights.

### Loss-handling policy

The strategy explicitly allows a losing position to remain open when:

- the asset remains eligible
- the broader regime is not decisively hostile
- long-horizon structure remains acceptable
- no hard exit condition has been reached

## Market Regime Logic

BTC acts as the primary market regime anchor in V1.
Regime logic incorporates:

- BTC trend direction
- BTC volatility state
- breadth across the tracked universe
- source-confidence health

The regime engine must classify the environment into at least these states:

- constructive
- neutral
- defensive
- frozen

These states control position scaling, new entries, and freeze behavior.

## Risk Framework

The risk framework is designed to maximize return while tolerating normal crypto drawdowns better than a tight-stop trend system.

### Risk philosophy

- avoid overreacting to ordinary volatility
- keep catastrophic-risk protection
- use de-risking in layers rather than relying on one hard stop

### Required risk controls

- position concentration cap
- USD cash fallback
- market-regime scaling
- data-integrity freeze
- execution-integrity freeze
- portfolio-level drawdown monitoring
- catastrophe protection rules for extreme market failure scenarios

### Portfolio drawdown policy

The system must monitor drawdown continuously.
V1 should not use a low-threshold forced liquidation rule that frequently ejects the portfolio during ordinary crypto drawdowns.
Instead, drawdown feeds a layered defense process:

- `elevated_caution`
- `reduced_aggressiveness`
- `catastrophe`
- `frozen`

The governing principle is fixed: drawdown alone should not trigger routine selling of otherwise supported positions, but it should reduce portfolio aggression progressively.
