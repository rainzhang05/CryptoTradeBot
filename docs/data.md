# Data Specification

## Data Policy Summary

Kraken is the authoritative market-data source for strategy decisions, backtests, simulation, and live evaluation.
Binance and Coinbase are supplementary sources only.

Supplementary sources may be used for:

- gap detection
- gap filling when Kraken data is incomplete and a documented fallback is approved
- cross-checking suspicious values
- data-integrity confidence reporting

Supplementary sources must not be used to create a blended synthetic primary price series for V1 strategy signals.

## Supported Exchanges

### Primary

- Kraken

### Supplementary

- Binance
- Coinbase

## Universe Scope

The canonical dataset only needs to support the fixed V1 universe:

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

The data layer may store additional metadata about related Kraken symbols if needed for validation or mapping, but trading and evaluation are scoped to the fixed V1 universe.

## Historical Data Sources

### Kraken

Kraken historical data may come from two paths:

- user-provided full historical data packages already downloaded locally
- Kraken API retrieval for dates not covered by the local dump

The operator-facing `cryptotradebot setup` workflow must also be able to bootstrap a runtime-ready
recent Kraken history window on a blank machine. That bootstrap is allowed to use the public Kraken
OHLC API to seed only the recent history required for live and simulate mode when no fuller local
history exists yet.

### Binance and Coinbase

Binance and Coinbase data should come only from free publicly available sources such as APIs or downloadable market-history endpoints permitted by those platforms.

## Canonical Data Requirements

The project must normalize all market data into a canonical local format.

### Required canonical properties

- UTC timestamps
- clear symbol mapping from exchange-native naming to project asset identifiers
- explicit source metadata
- deduplicated rows
- integrity status for each dataset segment
- local storage that supports deterministic reloads

### Required data classes

- OHLCV candles
- market metadata such as symbol status, lot size, and tick size where available
- optional order-book snapshots if the implementation later supports them, but they are not required for the initial canonical dataset

## Time Coverage Policy

- Use the full historical span available from Kraken for each asset.
- Extend beyond 2025-12-31 using API retrieval when the local dump stops there.
- Retain as much historical data as the local machine can handle comfortably.

For live and simulate readiness on a blank install, the setup workflow only needs to seed a recent
Kraken-native runtime window that comfortably exceeds the strategy lookback requirements. Full
research and long-horizon backtest history may still rely on user-provided Kraken dumps plus later
completion runs.

The project should optimize storage and caching rather than artificially truncating history.

## Integrity Rules

Every data ingestion process must check for:

- missing candles
- duplicate timestamps
- out-of-order rows
- non-positive prices where invalid
- malformed numeric fields
- symbol mismatches
- timezone inconsistencies

Every ingestion run must emit a data-integrity report.

## Gap Handling Policy

### Kraken complete and clean

If Kraken data is complete and passes integrity checks, use Kraken only.

### Kraken incomplete or suspicious

If Kraken data has a gap or anomaly:

- mark the affected interval
- cross-check the interval against Binance and Coinbase
- prefer repairing from Kraken if a trusted alternative Kraken source exists
- use supplementary data only under a documented fallback rule

When the operator explicitly runs a completeness repair workflow, the implementation may materialize a last-resort synthetic carry-forward candle only after Kraken, Binance, and Coinbase all fail to provide the missing interval. Any such candle must be traceable in metadata through its explicit source value and must not be mistaken for Kraken-native market data.

When the operator reruns the completeness repair workflow, the implementation should re-check previously non-Kraken candles and upgrade them to Kraken-native candles when Kraken later provides the same timestamps.

Any fallback filled segment must be traceable in metadata.

## Cross-Exchange Policy

Cross-exchange data exists for validation, not for price blending.

The project must not:

- average Kraken, Binance, and Coinbase prices into a single signal series
- rank assets using a synthetic cross-exchange price unless the docs are updated to allow it
- evaluate live strategy success on non-Kraken performance data

## Local Storage Policy

The repository implementation must support a local-first data layout suitable for a MacBook Pro.
The project may store large data artifacts outside version control while keeping manifests and metadata inside the repository where appropriate.

### Storage expectations

- raw source data is preserved when practical
- canonical cleaned datasets are materialized locally
- derived feature datasets are cacheable
- backtest and simulation outputs are stored as reproducible artifacts

Phase 3 derived-dataset layout is:

- `artifacts/features/<dataset_id>/dataset.csv`: deterministic point-in-time feature rows
- `artifacts/features/<dataset_id>/manifest.json`: deterministic input, settings, and column metadata
- `artifacts/experiments/<dataset_id>/`: optional reserved experiment root for future deterministic evaluation artifacts tied to that dataset

`dataset_id` must be deterministic from the selected assets, research settings, and canonical daily inputs so cached datasets can be reused safely.

## Symbol Mapping Policy

Kraken symbol naming can differ from common ticker conventions.
The implementation must provide a stable asset mapping layer so the project can refer to assets by the V1 identifiers while translating cleanly to Kraken-specific symbols and pair names.

## Evaluation Policy

- strategy evaluation is performed against Kraken-based canonical data
- supplementary data may inform integrity confidence but not official performance measurement
- any backtest that relies on fallback-filled intervals must report that fact

## Data Deliverables by Maturity

### Minimum useful dataset

- canonical OHLCV for all ten assets from Kraken
- incremental Kraken extension past the user-provided dump end date
- integrity reports for each asset
- a one-command runtime bootstrap path that seeds enough Kraken-native recent candles for
  `simulate` and `live` mode on a blank machine

### Production-ready dataset

- canonical OHLCV plus metadata for all ten assets
- gap and anomaly diagnostics
- supplementary exchange cross-check capability
- reproducible feature inputs for research, backtest, simulate, and live modes
- a repair workflow that can close historical gaps and extend canonical data to the latest closed interval with traceable source metadata
