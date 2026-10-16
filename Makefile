.PHONY: check lint types test guards fmt imports capital sandbox secrets verify-harness

check: lint types test guards

# The harness's own proof of itself: every real implementation passes the shared contract suite,
# every adversarial case is rejected at its expected stage, and one candidate runs end to end.
verify-harness:
	uv run pytest tests/contract_suite tests/adversarial tests/harness tests/e2e/test_harness_e2e.py -q

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

types:
	uv run mypy .

test:
	uv run pytest

guards: imports capital sandbox secrets

imports:
	uv run lint-imports

capital:
	uv run python -m tools.check_capital_literals

sandbox:
	uv run python -m tools.check_strategy_sandbox

secrets:
	git ls-files | grep -v '^uv\.lock$$' | xargs uv run detect-secrets-hook --baseline .secrets.baseline
