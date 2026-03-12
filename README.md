# Crypto Trade Bot

Kraken spot trading bot CLI with an interactive operator shell for research, simulation, and live operations.

<p align="center">
  <img src="docs/assets/tradebot-shell-sample.png" alt="Crypto Trade Bot shell sample" width="620">
</p>

Crypto Trade Bot packages the repository’s documented Kraken-only workflow into a single `cryptotradebot` command. On interactive terminals it opens the operator shell by default, while the full direct command surface remains available for automation, data preparation, backtesting, simulation, and live runtime tasks.

## Quickstart

Install `pipx` and the package:

```bash
python3 -m pip install --user pipx
pipx ensurepath
pipx install cryptotradebot
```

Open the shell from anywhere in your terminal with:

```bash
cryptotradebot
```

`cryptotradebot` launches the interactive operator shell after the package is installed.

On first launch, `cryptotradebot` creates the default application home under `~/.cryptotradebot/`.

The intended operator flow is:

```bash
cryptotradebot
setup
kraken auth set YOUR_API_KEY --secret YOUR_API_SECRET
run --mode live
```

## Disclaimer
Crypto asset trading involves substantial financial risk, including the possible loss of all capital. This software does not guarantee profitability, capital preservation, or suitability for any particular purpose.

Any historical performance information, including backtests, simulations, or paper-trading results, is provided for informational purposes only and does not guarantee future results. Market conditions, liquidity, execution quality, exchange behavior, and other real-world factors may cause live outcomes to differ materially from prior evaluations.

Users should trade only with assets they can afford to lose. Each user is solely responsible for properly configuring, testing, validating, and operating the software, and for any financial outcomes arising from its use. Use of this software is entirely at the user’s own risk.

## Docs

- [Crypto Trade Bot Documentation](docs/)
- [Commands](docs/shell-commands.md)

This repository is licensed under the [MIT License](LICENSE).
