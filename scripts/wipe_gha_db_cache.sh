#!/usr/bin/env bash
# Wipe cached Turso DBs from GitHub Actions for one or more cache legs.
#
# Usage:
#   scripts/wipe_gha_db_cache.sh <primary|deployment|market3|shared|buildercost|all>
#
# Env overrides:
#   GHA_CACHE_REF      git ref to scope the search (required — no default;
#                      the production cutover uses refs/heads/main, while
#                      implementation branches have separate caches)
#   GHA_CACHE_PREFIX   cache key prefix without leg suffix (default: turso-dbs-v4)

set -euo pipefail

usage() {
  echo "Usage: $0 <primary|deployment|market3|shared|buildercost|all>" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage

case "$1" in
  primary|deployment|market3|shared|buildercost) legs=("$1") ;;
  all)  legs=(primary deployment market3 shared buildercost) ;;
  *)    usage ;;
esac

REF="${GHA_CACHE_REF:?set GHA_CACHE_REF to the branch whose caches to wipe}"
PREFIX_BASE="${GHA_CACHE_PREFIX:-turso-dbs-v4}"

wipe_leg() {
  local leg="$1"
  local pattern

  # Market DBs live under <prefix>-mkt-<market>-<date>; the shared SDE+fitting
  # bundle under <prefix>-shared-<date> (see market-data-collection.yml).
  # The builder-cost cache is a separate key family with no leg infix at all
  # (see builder-costs-collection.yml) — it never shares PREFIX_BASE.
  if [[ "$leg" == "buildercost" ]]; then
    pattern="builder-cost-dbs-v4-"
  elif [[ "$leg" == "shared" ]]; then
    pattern="${PREFIX_BASE}-shared-"
  else
    pattern="${PREFIX_BASE}-mkt-${leg}-"
  fi

  local ids
  echo "Wiping caches matching ${pattern}* on ${REF}..."
  # gh paginates internally up to --limit; 5000 is well above any realistic cap
  # (daily key bucketing × 7-day GHA retention × 5 legs ≈ 35 entries max).
  # Loop guards against races where new caches arrive mid-deletion.
  while ids=$(gh cache list --limit 5000 --ref "$REF" --key "$pattern" \
                --json id --jq '.[].id') && [[ -n "$ids" ]]; do
    echo "$ids" | xargs -r -I{} gh cache delete {}
  done
  echo "Done: no remaining caches for ${pattern}*"
}

for leg in "${legs[@]}"; do
  wipe_leg "$leg"
done
