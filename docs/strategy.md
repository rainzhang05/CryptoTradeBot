# Strategy Specification

## Strategy Identity

The V1 strategy is a hybrid long-only spot allocation system for Kraken.
It combines a deterministic rule-based shell with an ML prediction layer.

The rule-based shell exists to provide market structure awareness, admissibility rules, portfolio constraints, and safety boundaries.
The ML layer exists to improve ranking quality and to make downside handling more selective so the system does not exit losing positions prematurely without strong evidence.

## Design Principles

- Trade only the fixed V1 universe.
- Hold cash in USD when not allocated.
- Use rule-based logic for hard constraints and market structure.
- Use ML for probabilistic forecasting and sell-quality refinement.
- Bias toward holding losing positions longer when the forward outlook remains acceptable.
- Avoid naive forced selling purely because a position is red versus entry.
- Allow the strategy to de-risk when both market structure and predictive evidence deteriorate materially.

## Strategy Architecture

### Layer 1: Deterministic rule shell

The rule shell provides:

- Universe enforcement.
- Signal windows and feature derivation boundaries.
- Market regime classification.
- Position-count and concentration limits.
- Cash-allocation policy.
- Emergency freeze conditions.
- Hard vetoes on trades when market-data or execution integrity is compromised.

### Layer 2: ML prediction layer

The ML layer provides per-asset predictive outputs that refine portfolio decisions.
The initial ML scope is explicitly limited to improving decision quality within the rule shell, not replacing it.

The ML layer must produce at least these outputs for each eligible asset:

- `expected_return_score`: estimated forward return attractiveness over the configured holding horizon.
- `downside_risk_score`: estimated probability or severity of adverse move over the configured downside horizon.
- `sell_risk_score`: estimated confidence that continuing to hold is materially worse than exiting or reducing.

The ML layer may later add uncertainty estimates, but V1 must remain auditable and bounded by the rule shell.

## Data Frequency and Decision Rhythm

The strategy operates primarily on end-of-day data and makes portfolio decisions at a fixed daily decision point.
Intraday monitoring exists for execution and severe deterioration handling, not for high-frequency trading.

### Initial decision cadence

- Primary strategy evaluation: once per day.
- Portfolio rebalance consideration: once per day.
- Execution monitoring: continuous while the bot is running.
- Retraining cadence: scheduled, versioned, and separate from live trading.

The exact clock times may be adjusted during implementation, but they must remain fixed, documented, and consistent across backtest, simulate, and live modes.

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

- The asset is tradable on Kraken for the configured USD pair.
- Canonical Kraken data is present and passes data-integrity checks.
- The latest feature set is complete.
- The asset is not blocked by exchange, runtime, or data-quality safeguards.

## Rule-Based Feature Set

The deterministic feature layer must include, at minimum:

- Multi-horizon price momentum.
- Long-horizon trend state.
- Realized volatility.
- Relative strength versus the fixed universe.
- Breadth across the universe.
- BTC-led market regime features.
- Volume and liquidity sanity checks where available.

These features must be computed entirely from the canonical Kraken dataset for strategy decisions.
Supplementary exchange data must not replace Kraken values in the primary signal set.

## Phase 3 Research Defaults

The Phase 3 implementation uses these deterministic daily research defaults:

- primary feature interval: 1 day
- momentum windows: 7, 30, and 90 trading days
- trend-gap windows: 50 and 200 trading days
- realized-volatility windows: 20 and 60 trading days
- relative-strength window: 30 trading days versus the fixed universe average
- breadth windows: 30 trading days for positive-momentum breadth and above-trend breadth
- liquidity window: 20 trading days using average dollar volume and trade count
- source-confidence window: 30 trading days using Kraken-versus-fallback source ratios

The initial Phase 3 labels are:

- forward-return label: 5-day close-to-close return
- downside label: minimum forward low over 10 days, plus a downside-risk flag at -8%
- sell-risk label: minimum forward low over 20 days combined with a 20-day return filter, flagged when drawdown is at least -12% and the 20-day return is at most -2%

The initial BTC-led regime classification is:

- `frozen` when recent BTC source confidence is below 80% or required BTC regime inputs are incomplete
- `constructive` when BTC momentum and BTC trend gap are positive and fixed-universe breadth is at least 60%
- `defensive` when BTC momentum is below -5%, BTC trend gap is below -3%, or fixed-universe breadth is at most 35%
- `neutral` otherwise

## ML Feature Policy

The ML layer may use:

- Canonical Kraken market features.
- Derived market-structure features.
- Regime features.
- Cross-checked data-quality indicators from Binance and Coinbase.

The ML layer must not use future information, blended future data, or non-point-in-time availability assumptions.

Phase 6 currently implements three supervised outputs from the deterministic feature store:

- expected-return regression for ranking support
- downside-risk classification for entry gating and defensive scaling
- sell-risk classification for stronger reduction and exit confirmation

Only predictions from the active promoted model that matches the current dataset may be consumed by the strategy engine.

## Portfolio Construction

### Asset count

- Maximum concurrent holdings in V1: 10.
- The strategy may hold fewer than 10 assets when evidence is weak.

### Weighting approach

Portfolio weights must be generated through a bounded scoring process that combines:

- Rule-based eligibility.
- Rule-based regime filter.
- ML expected return ranking.
- ML downside adjustment.
- Concentration limits.

The implementation should prefer normalized score-based allocation over equal weight because the project goal is to maximize expected return, but the final weights must remain capped for concentration control.

The implemented allocation path combines rule-based scores, normalized expected-return ranking when promoted predictions are available, downside penalties, regime scaling, and drawdown-aware risk-state scaling before final concentration and cash normalization.

### Concentration rule

- Maximum single-asset target weight in V1: 35%.

### Cash behavior

- The bot may hold partial or full USD cash when the strategy is insufficiently constructive.
- Going to cash is permitted and expected when market-wide evidence is poor.
- Cash is not treated as alpha generation; it is a defensive state.

## Entry Logic

An asset becomes a candidate for purchase or increased weight when:

- The rule shell marks the asset as eligible.
- The market regime is not blocked.
- The ML expected return score is positive enough to rank inside the desired portfolio set.
- The downside-risk score is below the configured exclusion threshold.

Entries are driven by relative attractiveness, not by dip-buying logic alone.

The current implementation also blocks new entries when predicted downside risk breaches the configured entry threshold even if the broader rule shell remains constructive.

## Exit and Sell Logic

The sell policy is intentionally not a standard tight-stop momentum approach.
It is designed to bias toward holding losers longer when the predictive outlook still supports recovery.

### Sell principles

- Do not exit solely because unrealized PnL is negative.
- Do not use a fixed percentage stop-loss from entry as a primary decision rule.
- Use combined evidence from market structure and ML downside prediction.
- Favor reduction over full liquidation when the asset weakens but the broader market remains constructive.
- Allow full exits when downside evidence becomes strong enough.

### Mandatory full exit conditions

The system must fully exit a position when any of the following are true:

- The asset becomes non-tradable or unsupported on Kraken.
- Data integrity is insufficient to continue holding responsibly.
- The runtime enters a system freeze state.
- A severe market-structure breakdown occurs and the ML sell-risk score confirms elevated downside.

### Gradual reduction conditions

The system should reduce but not necessarily fully exit when:

- Relative ranking weakens materially.
- Expected return score falls toward neutral.
- Downside risk rises but not enough to justify a full exit.
- Market breadth weakens while BTC remains constructive.

The implemented Phase 6 path also reduces positions when sell-risk or downside-risk predictions deteriorate before a hard forced-exit condition is reached.

### Loss-handling policy

The strategy explicitly allows a losing position to remain open when:

- The asset remains eligible.
- The broader regime is not decisively hostile.
- The ML expected return score remains supportive.
- The sell-risk score stays below the forced-exit threshold.

## Market Regime Logic

BTC acts as the primary market regime anchor in V1.
Regime logic must incorporate:

- BTC trend direction.
- BTC volatility state.
- Breadth across the fixed universe.
- Aggregate deterioration versus improvement signals.

The regime engine must classify the environment into at least these states:

- constructive
- neutral
- defensive
- frozen

These states control position scaling, new entries, and sell strictness.

## Risk Framework

The risk framework must fit the stated objective of maximizing return while tolerating normal crypto drawdowns better than a tight-stop trend system.

### Risk philosophy

- Avoid overreacting to ordinary volatility.
- Keep catastrophic-risk protection.
- Use de-risking in layers rather than relying on a single hard stop.

### Required risk controls

- Position concentration cap.
- USD cash fallback.
- Market regime scaling.
- Data-integrity freeze.
- Execution-integrity freeze.
- Portfolio-level drawdown monitoring.
- Catastrophe protection rules for extreme market failure scenarios.

### Portfolio drawdown policy

The system must monitor drawdown continuously.
V1 should not use a low-threshold forced liquidation rule that frequently ejects the portfolio during ordinary crypto drawdowns.
Instead, drawdown should feed a layered defense process:

- elevated caution state
- reduced aggressiveness state
- catastrophe state

The current implementation maps these layers to normal, elevated, stressed, and frozen portfolio risk states so exposure can be reduced progressively before a full freeze or catastrophe response is required.

The exact thresholds must be declared in implementation docs and validated by backtest evidence before live deployment, but the governing principle is fixed: drawdown alone should not trigger routine selling of otherwise supported positions.

## ML Modeling Requirements

### Modeling objectives

The ML subsystem must improve decisions in these areas:

- better relative ranking among the fixed universe
- better identification of weak entries to avoid
- better distinction between normal drawdown and truly deteriorating positions
- better timing for partial reduction or full exit when downside evidence is strong

### Validation requirements

- Walk-forward evaluation only.
- No leakage across train and test windows.
- Clear versioning of datasets, features, models, and results.
- Performance must be assessed on Kraken-based evaluation data.
- The ML layer must demonstrate incremental benefit over the rule-only baseline before promotion.

The current validation summaries track expected-return MAE, expected-return correlation, directional accuracy, downside Brier score, sell-risk Brier score, validation row count, and walk-forward split count.

## Strategy Promotion Rules

No strategy change, model change, or parameter change can be promoted to live use unless:

- It is documented.
- It is backtested on Kraken evaluation data.
- It is compared with the previous baseline.
- It passes simulation validation.
- It passes release gates in `testing-and-quality.md`.

## Explicit V1 Constraints

- No high-frequency trading.
- No blended cross-exchange execution logic.
- No discretionary manual overrides during normal live operation.
- No silent substitution of assets outside the fixed universe.
- No treating Binance or Coinbase as equal primary signal sources.