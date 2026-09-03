"""The workflows must not hardcode database filenames.

Every hardcoded name is a second place the production switch has to be made,
and the two drift silently: a stale cache path restores nothing and the run
cold-pulls, which looks like success.
"""
import re
from pathlib import Path

import pytest

from mkts_backend.config.settings_service import SettingsService

WORKFLOWS = [
    Path(".github/workflows/market-data-collection.yml"),
    Path(".github/workflows/builder-costs-collection.yml"),
]


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_no_database_filename_is_hardcoded(wf):
    text = wf.read_text()
    files = {c["file"] for c in SettingsService().database_routing().values()}
    found = sorted(f for f in files if f in text)
    assert found == [], f"{wf.name} hardcodes {found}"


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_no_bare_db_filename_pattern_remains(wf):
    """Catches a renamed database the fixture above would miss.

    The trailing ``(?!\\.)`` excludes dotted Python module paths such as
    ``mkts_backend.db.models`` (from the smoke-import step's module list),
    which are not database filenames: a real filename in this YAML is always
    followed by whitespace, a quote, end of line, or a glob ``*`` — never by
    another ``.``.
    """
    hits = re.findall(
        r"\b\w*(?:mkt|sde|fitting|buildcost)\w*\.db\b(?!\.)", wf.read_text()
    )
    assert hits == [], f"{wf.name} still names {sorted(set(hits))}"
