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

- User-provided full historical data packages already downloaded locally.
- Kraken API retrieval for dates not covered by the local dump.

### Binance and Coinbase

Binance and Coinbase data should come only from free publicly available sources such as APIs or downloadable market-history endpoints permitted by those platforms.

## Canonical Data Requirements

The project must normalize all market data into a canonical local format.

### Required canonical properties

- UTC timestamps.
- Clear symbol mapping from exchange-native naming to project asset identifiers.
- Explicit source metadata.
- Deduplicated rows.
- Integrity status for each dataset segment.
- Local storage that supports deterministic reloads.

### Required data classes

- OHLCV candles.
- Market metadata such as symbol status, lot size, and tick size where available.
- Optional order-book snapshots if the implementation later supports them, but they are not required for the initial canonical dataset.

## Time Coverage Policy

- Use the full historical span available from Kraken for each asset.
- Extend beyond 2025-12-31 using API retrieval when the local dump stops there.
- Retain as much historical data as the local machine can handle comfortably.

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

- Raw source data is preserved when practical.
- Canonical cleaned datasets are materialized locally.
- Derived features and labels are cacheable.
- Backtest and simulation outputs are stored as reproducible artifacts.

Phase 3 derived-dataset layout is:

- `artifacts/features/<dataset_id>/dataset.csv`: experiment-ready labeled rows
- `artifacts/features/<dataset_id>/manifest.json`: deterministic input, settings, and column metadata
- `artifacts/experiments/<dataset_id>/`: reserved experiment root for later training and validation artifacts tied to that dataset

`dataset_id` must be deterministic from the selected assets, research settings, and canonical daily inputs so cached datasets can be reused safely.

## Symbol Mapping Policy

Kraken symbol naming can differ from common ticker conventions.
The implementation must provide a stable asset mapping layer so the project can refer to assets by the V1 identifiers while translating cleanly to Kraken-specific symbols and pair names.

## Evaluation Policy

- Strategy evaluation is performed against Kraken-based canonical data.
- Supplementary data may inform integrity confidence but not official performance measurement.
- Any backtest that relies on fallback-filled intervals must report that fact.

## Data Deliverables by Maturity

### Minimum useful dataset

- Canonical OHLCV for all ten assets from Kraken.
- Incremental Kraken extension past the user-provided dump end date.
- Integrity reports for each asset.

### Production-ready dataset

- Canonical OHLCV plus metadata for all ten assets.
- Gap and anomaly diagnostics.
- Supplementary exchange cross-check capability.
- Reproducible feature inputs for research, backtest, simulate, and live modes.
- A repair workflow that can close historical gaps and extend canonical data to the latest closed interval with traceable source metadata.