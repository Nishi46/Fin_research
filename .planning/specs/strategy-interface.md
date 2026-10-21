# Spec: Strategy interface (build step 2, completed)

**Created**: 2026-10-20
**Status**: owner approved defining it now (2026-10-20); design below follows the review finding that
the stub lacked what the pipeline already requires.
**Author**: Nishi Kantamneni
**Epic**: none

## Problem

Build step 2 left a `Strategy` protocol that only says "has an id and a `generate`". The pipeline
needs more: a `build(params)` callable and a params mapping to evaluate parameter neighbours
(stability and the neighbour PBO), and it assumes a strategy keeps no state between calls (the
lookahead check compares a truncated run with the full run). None of that is written down, so a
strategy author, or the proposer agent, cannot learn the required shape from the interface, and
`BaselineReversal` does not satisfy it.

## Goal

A strategy is any object that satisfies `ParametrizedStrategy`, and a conformance suite checks the
rules below for every strategy in `strategies/`. `run_pipeline` can be called with just the strategy.

## The convention

A `ParametrizedStrategy` is a `Strategy` that also has:

1. `params`: a read-only mapping `name -> int | float` (JSON-scalar). Exactly the numeric knobs that
   change its behaviour. Returned fresh each time, so callers cannot mutate the strategy through it.
2. `with_params(params)`: returns a NEW instance of the same strategy with those params. It never
   mutates `self`. `with_params(self.params)` must behave identically to `self` (the pipeline
   already proves this before it trusts a strategy's neighbours).
3. `lookback_bars`: the minimum number of sessions of history before `generate` can return a
   non-empty intent. Fewer bars than that must return an empty `Intent`, never raise.

Behavioural rules, checked by the conformance suite:

- **Stateless and deterministic.** `generate` is a pure function of `(data, asof, params)`. Two calls
  with the same inputs give equal intents, and calling out of date order gives the same answers as
  calling in order.
- **No mutation.** `generate` and `with_params` do not change `self` or the mapping they were given.
- **Universe.** A strategy may only name symbols it can see in `data`; an intent naming anything else
  makes the engine raise (existing behaviour, unchanged).
- **Errors.** A strategy that raises is logged by the pipeline as an ERROR trial at the stage it
  failed in. It must not swallow errors to look healthy.
- **Imports** stay limited to the allow-list in `tools/check_strategy_sandbox.py`.

Out of scope here (build step 5 design): per-call timeouts and the process-level sandbox that runs
strategy code away from the grader; a loader or discovery mechanism for `strategies/`.

## Requirements

1. `contracts/strategy.py` defines `ParamValue`, `Params` and the `ParametrizedStrategy` protocol, and
   `contracts/__init__.py` exports them. The plain `Strategy` protocol is unchanged, so existing test
   strategies keep working.
2. `strategies/baseline.py` conforms.
3. `tests/contract_suite/` gains a strategy conformance suite covering every rule above, run against
   `BaselineReversal` and a set of deliberately broken strategies that must each fail the matching
   check (stateful, mutating params, raising below lookback, `with_params` that ignores its argument).
4. `run_pipeline` accepts `build` and `params` as optional: when omitted and the strategy is a
   `ParametrizedStrategy`, it uses `strategy.with_params` and `strategy.params`. Passing them
   explicitly still works and wins.

## Edge cases

1. A strategy with no tunable parameters: `params` is an empty mapping; stability cannot be judged
   (existing "too few configurations" failure at STABILITY), which is the honest outcome.
2. `with_params` given an unknown or non-numeric name: raises `ValueError`; the pipeline logs ERROR.
3. `lookback_bars` of 0: allowed, means no warm-up needed.
4. A strategy with hidden state set in `generate` (e.g. a cache): the conformance suite fails it.

## Testing criteria

Conformance suite as above; `BaselineReversal` passes; each broken strategy fails exactly the intended
check; the existing pipeline and engine tests pass unchanged.
