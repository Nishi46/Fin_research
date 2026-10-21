#!/usr/bin/env bash
# Run a command inside the proposer sandbox. See docs/PROPOSER_ISOLATION.md (option B).
#
#   sandbox/run_proposer.sh <command...>
#
# What the container gets: the repo READ-ONLY, strategies/ read-write (its only write target),
# optionally the research data cache read-only. It gets NO holdout directory, NO .env, NO broker
# or Alpaca keys, NO Docker socket, and NO network unless PROPOSER_NETWORK is set.
# The gate runs OUTSIDE this container, as the owner.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${PROPOSER_IMAGE:-python:3.12-slim}"
NETWORK="${PROPOSER_NETWORK:-none}"        # none = no egress at all (default)
CPUS="${PROPOSER_CPUS:-2}"
MEMORY="${PROPOSER_MEMORY:-2g}"
PIDS="${PROPOSER_PIDS:-256}"

args=(
  run --rm
  --network "$NETWORK"
  --user 10001:10001
  --read-only
  --tmpfs /tmp:rw,noexec,nosuid,size=256m
  --cap-drop ALL
  --security-opt no-new-privileges
  --cpus "$CPUS" --memory "$MEMORY" --pids-limit "$PIDS"
  --workdir /repo
  -v "$REPO":/repo:ro
  -v "$REPO/strategies":/repo/strategies:rw
  # Deliberately not mounted: the holdout directory, .env, ~/.claude, /var/run/docker.sock.
  -e HOME=/tmp
  -e PYTHONDONTWRITEBYTECODE=1
)

# The repo mount contains the owner's real .env (Alpaca keys). Mask every .env* file with an empty
# one so the container sees the path but no secrets. .env.example is harmless and left alone.
while IFS= read -r envfile; do
  rel="${envfile#"$REPO"/}"
  args+=(-v /dev/null:"/repo/$rel":ro)
done < <(find "$REPO" -maxdepth 3 \( -name .venv -o -name node_modules -o -name .git \) -prune -o \
  -type f \( -name '.env' -o -name '.env.*' \) ! -name '.env.example' -print)

# The research cache is not secret (research data); mount it read-only if the owner set it.
if [[ -n "${LAB_DATA_CACHE_DIR:-}" && -d "${LAB_DATA_CACHE_DIR/#\~/$HOME}" ]]; then
  args+=(-v "${LAB_DATA_CACHE_DIR/#\~/$HOME}":/data/cache:ro)
fi

exec docker "${args[@]}" "$IMAGE" "$@"
