"""
Regression coverage for the removal of ``sync_equiv_to_remote``.

``sync_equiv_to_remote()`` read the local ``module_equivalents`` table, then
deleted and reinserted that same local table through ``remote_engine`` (an
alias of ``engine`` under pyturso) and never pushed. It was a fake stand-in
for a push that now exists — see ``equiv_manager.py``'s market loops, which
call ``DatabaseConfig(market_context=...).push()`` after each write
(``tests/test_management_push.py::TestEquivPush``).
"""

from mkts_backend.db import equiv_handlers


class TestSyncEquivToRemoteRemoved:
    """sync_equiv_to_remote() read the local table, then deleted and
    reinserted that same local table through remote_engine (an alias of
    engine) and never pushed. push() replaces it."""

    def test_function_is_gone(self):
        assert not hasattr(equiv_handlers, "sync_equiv_to_remote")

    def test_add_equiv_group_no_longer_references_sync(self):
        """Guard against a re-introduced call site: add_equiv_group's source
        must not mention the deleted function by name."""
        import inspect

        source = inspect.getsource(equiv_handlers.add_equiv_group)
        assert "sync_equiv_to_remote" not in source

    def test_remove_equiv_group_no_longer_references_sync(self):
        import inspect

        source = inspect.getsource(equiv_handlers.remove_equiv_group)
        assert "sync_equiv_to_remote" not in source
