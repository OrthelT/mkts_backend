#!/usr/bin/env bash
set -euo pipefail

# Every pyturso sidecar suffix a replica can have, plus the bare .db itself
# (the empty string). DB_FILES entries already include ".db", so every
# check/delete concatenates the suffix directly: "${db}${suffix}".
SUFFIXES=("" "-shm" "-wal" "-info" "-changes" "-wal-revert")

usage() {
    echo "Usage: $0" >&2
    echo "Deletes every database file (and pyturso sidecars) named by" >&2
    echo "'uv run mkts-backend --list-db-paths' for the active settings.toml." >&2
    exit 2
}

[[ $# -eq 0 ]] || usage

# The file list comes from whichever settings.toml is active, never a
# hardcoded copy — that property is the whole point of this migration.
mapfile -t DB_FILES < <(uv run mkts-backend --list-db-paths | cut -f2)

db_exists() {
    local db=$1
    local suffix

    for suffix in "${SUFFIXES[@]}"; do
        [[ -e "${db}${suffix}" ]] && return 0
    done
    return 1
}

preview_deletes() {
    local db
    local suffix

    for db in "$@"; do
        if db_exists "$db"; then
            echo "Will delete:"
            for suffix in "${SUFFIXES[@]}"; do
                echo "  ${db}${suffix}"
            done
        else
            echo "Files not found for: ${db}"
        fi
    done
}

confirm() {
    local response

    while true; do
        read -r -p "Proceed with deletion and refresh? [Y/n] " response ||
            response="n"

        case "$response" in
            "" | [Yy])
                return 0
                ;;
            [Nn])
                echo "Operation cancelled."
                exit 0
                ;;
            *)
                echo "Please enter Y or n."
                ;;
        esac
    done
}

delete_files() {
    local db
    local suffix
    local paths

    for db in "$@"; do
        if db_exists "$db"; then
            paths=()
            for suffix in "${SUFFIXES[@]}"; do
                paths+=("${db}${suffix}")
            done
            rm -f "${paths[@]}"
            echo "Deleted files for: ${db}"
        else
            echo "Files not found for: ${db}"
        fi
    done
}

verify_files() {
    local db
    local failed=0

    for db in "$@"; do
        if db_exists "$db"; then
            echo "Files still exist for: ${db}" >&2
            failed=1
        else
            echo "Verified deleted: ${db}"
        fi
    done

    return "$failed"
}

echo "Refreshing databases..."
echo "Removing current instances..."
echo "--------------"

preview_deletes "${DB_FILES[@]}"
confirm
delete_files "${DB_FILES[@]}"
verify_files "${DB_FILES[@]}"

echo "Operation complete."
