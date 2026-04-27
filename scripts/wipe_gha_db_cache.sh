#!/usr/bin/env bash
# Wipe cached Turso DBs from GitHub Actions for one or both market matrix legs.
#
# Usage:
#   scripts/wipe_gha_db_cache.sh <primary|deployment|both>
#
# Env overrides:
#   GHA_CACHE_REF      git ref to scope the search (default: refs/heads/main)
#   GHA_CACHE_PREFIX   cache key prefix without market suffix (default: turso-dbs-v2)

set -euo pipefail

usage() {
  echo "Usage: $0 <primary|deployment|both>" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage

case "$1" in
  primary|deployment) markets=("$1") ;;
  both)               markets=(primary deployment) ;;
  *)                  usage ;;
esac

REF="${GHA_CACHE_REF:-refs/heads/main}"
PREFIX_BASE="${GHA_CACHE_PREFIX:-turso-dbs-v2}"

wipe_market() {
  local market="$1"
  local pattern="${PREFIX_BASE}-${market}-"
  local ids

  echo "Wiping caches matching ${pattern}* on ${REF}..."
  # gh paginates internally up to --limit; 5000 is well above any realistic cap
  # (hourly schedule × 7-day GHA retention × 2 markets ≈ 336 entries max).
  # Loop guards against races where new caches arrive mid-deletion.
  while ids=$(gh cache list --limit 5000 --ref "$REF" --key "$pattern" \
                --json id --jq '.[].id') && [[ -n "$ids" ]]; do
    echo "$ids" | xargs -r -I{} gh cache delete {}
  done
  echo "Done: no remaining caches for ${pattern}*"
}

for m in "${markets[@]}"; do
  wipe_market "$m"
done
