# Release Checklist

- The repository installs from a clean checkout.
- `.venv/bin/python -m pytest` passes.
- Coverage remains at or above 80%.
- Docker validation workflows are green.
- Canonical data can be imported, checked, and completed from a clean workspace.
- The repository can build features, run a backtest, and run simulate mode from a fresh workspace.
- `tradebot doctor` succeeds for the intended release environment.
- `tradebot backtest run --strategy-preset live_default` succeeds and writes the expected artifacts.
- `tradebot run --mode simulate --max-cycles 1` succeeds and writes the expected runtime state.
- Live mode uses the documented deterministic strategy path and still freezes when data or execution prerequisites fail.
- The docs in `docs/` match the implemented operator surface and runtime behavior.
