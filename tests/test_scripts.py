"""Shell helpers must not carry their own copy of the database list.

dbrefreshtest.sh deleted `${db}.info` — a name that never existed — so the
real -info survived every "refresh" alongside a stale -changes queue.
"""
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
    text = Path("scripts/wipe_gha_db_cache.sh").read_text()
    assert "builder-cost-dbs-v4" in text


def test_wipe_script_does_not_default_to_main():
    """The caller must name the exact cache ref; an implicit ref can wipe
    the wrong family or silently find nothing."""
    text = Path("scripts/wipe_gha_db_cache.sh").read_text()
    assert "refs/heads/main}" not in text
