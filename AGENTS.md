# Repository Agent Instructions

Read the `docs/` folder before implementing anything in this repository.
The documentation in `docs/` is the only source of truth for the project.
Do not treat the PDF report in the repository root as authoritative.

## Required Reading Order

1. `docs/README.md`
Purpose: explains the documentation map and tells you which file governs which part of the project.

2. `docs/roadmap.md`
Purpose: defines the implementation phases, phase boundaries, and required deliverables.

3. `docs/specification.md`
Purpose: defines the product scope, fixed decisions, goals, non-goals, and final deliverable.

4. `docs/strategy.md`
Purpose: defines the authoritative hybrid trading strategy, sell logic, and risk philosophy.

5. `docs/data.md`
Purpose: defines the market-data policy, canonical data rules, and permitted use of Kraken, Binance, and Coinbase data.

6. `docs/architecture.md`
Purpose: defines the software architecture, module boundaries, runtime expectations, and Docker direction.

7. `docs/cli.md`
Purpose: defines the CLI command structure, naming rules, and runtime surface.

8. `docs/testing-and-quality.md`
Purpose: defines the test requirements, CI requirements, coverage threshold, and completion rules.

9. `docs/operations.md`
Purpose: defines logging, monitoring, alerting, freeze handling, and runbook expectations.

## Authority Rules

- If implementation and docs differ, the docs win.
- If a task requires a new major decision, update the docs first or in the same logical change.
- Do not silently change the fixed V1 asset universe, exchange scope, or runtime modes.

## Engineering Rules

- Add tests for every meaningful code change.
- Keep overall test coverage at or above 80%.
- Update or create GitHub Actions workflows whenever the project changes in a way that affects build, test, coverage, Docker, or validation behavior.
- Ensure workflow checks cover, as applicable: tests passing, code compiling or importing correctly, Docker building correctly, and coverage threshold enforcement.
- Do not finish a work session while workflows are knowingly broken unless the blocker is explicitly documented.

## Commit Rules

- Commit changes locally in small, logical increments.
- Do not batch an entire phase or a whole work session into one commit.
- Each commit should represent one coherent step such as a module, a spec update, a test addition, a workflow change, or a focused refactor.
- When practical, include implementation, tests, and docs for that logical step in the same commit.

## Validation Rules

- Run the relevant tests after changes.
- Run or update CI-related validation when workflows should change.
- Ensure Docker-related validation is added or updated when the runtime or packaging changes.
- Before ending a work session, leave the repository in a state where the intended workflow checks should pass.

## Documentation Maintenance Rules

- Update the relevant files in `docs/` when behavior, interfaces, commands, data policy, or strategy logic change.
- Keep `docs/roadmap.md` phase-oriented.
- Keep the other docs specification-oriented.

## Implementation Direction

- This project is a narrow, single-strategy Kraken spot bot, not a generic multi-strategy framework.
- V1 is CLI-only.
- V1 supports two modes only: `simulate` and `live`.
- USD is the only operating currency.
- Kraken is the authoritative execution and evaluation venue.
- Binance and Coinbase are supplementary validation sources only.