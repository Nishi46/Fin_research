# Proposer isolation (precondition for build step 5)

Status: design and runbook only. Nothing here is set up yet, and the proposer agent does not exist.
This must be in place and tested before the proposer agent is built or run.

## Why this exists

The invariant is that the proposer never sees the hidden holdout and cannot change what grades it.
The code enforces what it can (`ResearchWindow`, the manifest-checking `HoldoutGate`, the
protected-paths hook), but all of that runs as the owner's OS user. A process running as the owner
can read `~/lab-holdout`, `.env` (with the Alpaca keys), the registry, and the harness source, and can
write anywhere the owner can. File permissions (0700) and the Claude Code read-deny rule only stop
other users and Claude's own file tools. So the proposer must run as something that is not the owner.

## Threat model

The proposer is an LLM agent writing Python into `strategies/`. Assume it may, by accident or by
being steered, try to: read the holdout directory; read `.env` or other secrets; edit the harness,
thresholds or hooks; call the gate with its own runner (which receives the holdout path); open
network connections to exfiltrate data; place orders; or run unbounded work.

Not in scope: a malicious owner, or a kernel or container escape.

## Where the strategy code runs is the hard part (found in the 2026-10-20 review)

Today a strategy is a Python object handed to `run_pipeline` and called inside the grader's own
process. For the hidden-holdout run, that process holds the holdout path, the manifest, the ledger and
a provider over the holdout bars. So proposer-written code would execute with exactly the access the
isolation is meant to deny, whatever container the proposer itself lives in. `DataView` hides its
provider only by convention (`_provider` is a plain attribute), and the AST tripwire in
`tools/check_strategy_sandbox.py` says of itself that it is not a sandbox.

Keeping the proposer's own process isolated is therefore not enough. Strategy code must be executed in
a separate, sandboxed process during grading, and:
- it receives data only through a narrow channel (for example bars up to `asof` serialised to it per
  call), never a path to the holdout directory or the registry, and never a provider object;
- the holdout run feeds it holdout bars the same way, while the grader process keeps the files;
- it returns an `Intent` and nothing else; a timeout kills it; it has no network and no keys.

The container scripts in `sandbox/` are a starting point for that sandbox, but the run loop that calls
strategies across a process boundary does not exist yet. It is build step 5 design work and the main
open item, ahead of the container choice.

## What the proposer may and may not touch

| Resource | Proposer access |
|---|---|
| `strategies/` | read and write (its only write target) |
| `harness/`, `contracts/`, `tests/`, docs | read only (it may read the grader, never write it) |
| Research data cache (`~/lab-data/cache`) | read only |
| Holdout directory (`~/lab-holdout`) | none: not mounted, not readable |
| `.env`, broker keys, Alpaca keys, registry DB | none |
| Network | only what it needs to reach the model API (if it is a hosted model); nothing else |
| Gate / pipeline | only through a narrow entry point that runs OUTSIDE its sandbox and returns the stage reached and, for the holdout, a bare pass/fail |

The last row is the important one: `HoldoutGate.evaluate(run=...)` hands the holdout path to a
callable. If the proposer can supply that callable, it can read the holdout. So the gate and the
runner are built and run by the harness side (owner user or a harness service), and the proposer
only submits strategy code and receives a verdict.

## Option A: a separate macOS user (simplest on this machine)

1. Create a standard (non-admin) user, for example `labproposer`, with no access to the owner's home.
   Needs `sudo`; do it in System Settings or `sudo sysadminctl -addUser labproposer ...`.
2. Keep `~/lab-holdout` and `.env` at mode 0700 owned by the owner (already true for the holdout
   directory). Confirm the repo is not world-writable.
3. Give `labproposer` a checkout it can read, plus one writable `strategies/` directory (a separate
   directory or a group with write access only there). The harness runs as the owner and imports
   strategies from that directory by path.
4. Run the proposer as `labproposer` (for example `sudo -u labproposer ...` or a launchd job under
   that user). No broker keys, no Alpaca keys, no `.env` in its environment.
5. Network: macOS makes per-user egress rules awkward (pf rules, or a local proxy). If the model API
   is the only egress needed, allow only that host.

Strengths: no extra software, real kernel-enforced file permissions. Weaknesses: egress control is
fiddly; the same machine still holds everything; a mistake in step 2 silently weakens it.

## Option B: a container (Docker Desktop)

Run the proposer in a container with:
- the repo mounted read-only, and `strategies/` as the only read-write bind mount;
- the research cache mounted read-only;
- **no** mount of the holdout directory, `.env`, the registry or any key material;
- a non-root user, `--read-only` root filesystem with a tmpfs for scratch, `--cap-drop ALL`,
  `--security-opt no-new-privileges`, CPU and memory limits;
- network off, or an allow-list proxy to the model API only.

The gate runs outside the container (as the owner) and takes strategy files from the `strategies/`
mount. Strengths: egress and resource limits are easy, and the mounts are explicit and reviewable.
Weaknesses: Docker Desktop on macOS runs a VM (file sharing semantics differ), and the Docker
daemon itself is root-equivalent, so never mount the Docker socket into the container.

## Recommendation

Use B (container) for the proposer, with the gate on the owner side, and keep the A-style discipline
(0700 on secrets) as a second layer. If Docker is unwanted, A is acceptable provided the egress
question is answered.

## Implemented for option B (2026-10-20, written but not yet run)

- `sandbox/run_proposer.sh <command...>` starts a container with the hardening above: repo read-only,
  `strategies/` the only read-write mount, research cache read-only, every `.env*` file masked with an
  empty file (the repo mount contains the real `.env` with the Alpaca keys), no holdout directory, no
  Docker socket, no network by default (`PROPOSER_NETWORK` opts in), non-root, read-only root, all
  capabilities dropped, CPU, memory and process limits.
- `sandbox/verify_isolation.sh` runs the checklist below through that same launcher and exits non-zero
  if any "must fail" line succeeds.
- Status: the scripts' syntax and the exact `docker run` arguments were checked with a stand-in
  `docker`; they have NOT been run, because the Docker daemon was not running. To run them: start
  Docker Desktop, then `sandbox/verify_isolation.sh` (first run pulls `python:3.12-slim`).
- Still open: how the proposer reaches its model API with the network off (a proxy allow-list), where
  the gate runs and how code is submitted to it, and per-candidate resource limits.

## Verification checklist (run these before enabling the proposer, and after any change)

Run each as the proposer's identity (the other user, or inside the container). Every line must fail.

- [ ] `ls ~/lab-holdout` and `cat` of any file in it: permission denied or not found.
- [ ] Read `.env`, `~/.claude`, the Alpaca or broker keys: denied or absent.
- [ ] Write to anything under `harness/`, `contracts/`, `tests/`, `.claude/`, `pyproject.toml`: denied.
- [ ] Write to `strategies/`: succeeds (this is the only write that should).
- [ ] `import harness` works read-only; importing and calling `HoldoutGate(...).evaluate(...)` with
      a callable it wrote, pointed at the real holdout, reads nothing (the path is unreachable).
- [ ] Outbound connection to a host other than the model API: blocked.
- [ ] Place an order or reach the broker: impossible (no keys, no route).
- [ ] The verdict channel returns only stage and pass/fail; no t-statistic, returns, dates or counts.

## Open questions

1. Container or separate user? (Recommended: container.)
2. Is the proposer a hosted model reached over the network, or local? That decides the egress rule.
3. Where does the gate service run, and how does the proposer submit code to it (a CLI that copies a
   strategy file, a watched directory, a local socket)? This is part of build step 5's design.
4. Resource limits per candidate (CPU time, memory) so a runaway strategy cannot stall the machine.
