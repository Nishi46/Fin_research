#!/usr/bin/env bash
# Verify the proposer sandbox against the checklist in docs/PROPOSER_ISOLATION.md.
# Run it from the owner's shell after starting Docker Desktop. Every "must fail" line must fail.
#
#   sandbox/verify_isolation.sh
#
# Exit status 0 only if every check behaves as required. It never touches the real holdout.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$HERE/run_proposer.sh"
fail=0

# $1 = label, $2 = "pass" (command must succeed) or "block" (command must fail), rest = sh -c script
check() {
  local label="$1" want="$2" script="$3"
  if "$RUN" sh -c "$script" >/dev/null 2>&1; then got=pass; else got=block; fi
  if [[ "$got" == "$want" ]]; then
    printf 'ok    %s\n' "$label"
  else
    printf 'FAIL  %s (expected %s, got %s)\n' "$label" "$want" "$got"
    fail=1
  fi
}

HOLDOUT="${FIN_RESEARCH_HOLDOUT_DIR:-$HOME/lab-holdout}"

check "runs as a non-root user"                        pass  '[ "$(id -u)" != "0" ]'
check "cannot see the holdout directory by host path"  block "ls '$HOLDOUT'"
check "cannot see any holdout path under /"            block 'ls /holdout /lab-holdout /Users 2>/dev/null | grep -q .'
check "the repo's .env is masked (empty or absent)"    block 'test -s /repo/.env'
check "no Alpaca or broker keys in the environment"    block 'env | grep -Ei "ALPACA|LAB_BROKER|APCA"'
check "no Docker socket"                               block 'test -e /var/run/docker.sock'
check "cannot write under /repo/harness"               block 'touch /repo/harness/x'
check "cannot write under /repo/contracts"             block 'touch /repo/contracts/x'
check "cannot write under /repo/tests"                 block 'touch /repo/tests/x'
check "cannot write pyproject.toml"                    block 'echo x >> /repo/pyproject.toml'
check "cannot write the root filesystem"               block 'touch /usr/x'
check "can write strategies/ (its only write target)"  pass  'touch /repo/strategies/.sandbox_probe && rm /repo/strategies/.sandbox_probe'
check "can read the harness source"                    pass  'test -r /repo/harness/validation/holdout.py'
check "no outbound network (default)" block "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 443), 3)\""

echo
if [[ $fail -eq 0 ]]; then
  echo "isolation checks passed"
else
  echo "ISOLATION CHECKS FAILED - do not enable the proposer"
fi
exit $fail
