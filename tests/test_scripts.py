"""Shell helpers must not carry their own copy of the database list.

dbrefreshtest.sh deleted `${db}.info` — a name that never existed — so the
real -info survived every "refresh" alongside a stale -changes queue.
"""
import re
from pathlib import Path

from mkts_backend.config.settings_service import SettingsService


def test_dbrefreshtest_is_gone():
    assert not Path("dbrefreshtest.sh").exists()


def test_dbdeltest_does_not_hardcode_database_names():
    text = Path("dbdeltest.sh").read_text()
    files = {c["file"] for c in SettingsService().database_routing().values()}
    found = sorted(f for f in files if f.removesuffix(".db") in text)
    assert found == [], f"dbdeltest.sh hardcodes {found}"


def test_wipe_script_covers_builder_cost_caches():
    """buildercost must be a reachable leg — its own case arm, and a member
    of the `all` leg list — not just a spelling that happens to survive
    somewhere in the file (e.g. only in a comment or dead pattern branch
    after the case arm was dropped)."""
    text = Path("scripts/wipe_gha_db_cache.sh").read_text()

    case_arms = re.findall(r"^\s*([\w|]+)\)\s+legs=", text, re.MULTILINE)
    assert case_arms, "no case arms found in wipe_gha_db_cache.sh"
    single_leg_arms = [arm for arm in case_arms if arm != "all"]
    assert any("buildercost" in arm.split("|") for arm in single_leg_arms), (
        f"buildercost is not a selectable case arm; arms were {single_leg_arms}"
    )

    all_match = re.search(r"^\s*all\)\s+legs=\(([^)]*)\)", text, re.MULTILINE)
    assert all_match, "no `all)` case arm found in wipe_gha_db_cache.sh"
    assert "buildercost" in all_match.group(1).split(), (
        f"buildercost is missing from the `all` leg list: {all_match.group(1)!r}"
    )

    # And it must resolve to the real, no-leg-infix key shape from
    # builder-costs-collection.yml (Task 18), not just a recognized word.
    assert "builder-cost-dbs-v4-" in text


def test_wipe_script_does_not_default_to_main():
    """The caller must name the exact cache ref via `:?` (required) with no
    `:-` default at all. An implicit default of *any* value — not just the
    literal refs/heads/main — reproduces the same "silently wipes the wrong
    branch's caches, or finds nothing" defect."""
    text = Path("scripts/wipe_gha_db_cache.sh").read_text()
    ref_line = next(
        line for line in text.splitlines() if line.strip().startswith("REF=")
    )
    assert ":?" in ref_line, f"GHA_CACHE_REF must be required via :? — {ref_line!r}"
    assert ":-" not in ref_line, f"GHA_CACHE_REF must not default via :- — {ref_line!r}"
