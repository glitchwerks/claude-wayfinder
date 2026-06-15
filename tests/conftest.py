"""Top-level pytest configuration for the claude_wayfinder test suite.

Test-log isolation (issue #349)
--------------------------------
Every test in this suite runs with ``DISPATCH_LOG_PATH`` unset, so that
the matcher's logging path resolver returns ``None`` and log writing is
silently disabled.  This prevents pytest sessions from appending fixture
prompt rows to the developer's live dispatch log
(``~/.claude/state/dispatch-log.jsonl``).

The isolation is applied via the ``isolate_dispatch_log`` autouse fixture
below.  It runs at *function* scope so each test starts with the same
clean environment.  Tests that explicitly need logging set their own
``DISPATCH_LOG_PATH`` (via ``monkeypatch.setenv`` or the ``extra_env``
dict passed to the subprocess helper ``_run``); those per-test values
take precedence within the test's own scope because the autouse fixture
has already run its setup phase and yielded before any per-test fixture
setup begins.

See ``tests/test_log_isolation.py`` for the regression guard that
proves this fixture is active during every test run.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_dispatch_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset DISPATCH_LOG_PATH for every test to prevent live-log pollution.

    When ``DISPATCH_LOG_PATH`` is absent the matcher's
    ``_resolve_log_path()`` returns ``None``, and
    ``_write_log_entry`` returns immediately without writing anything.

    Tests that need to assert logging behaviour supply their own path via
    ``monkeypatch.setenv("DISPATCH_LOG_PATH", str(tmp_path / "log.jsonl"))``
    or by passing an explicit ``log_path`` argument to ``_write_log_entry``
    directly; both approaches are unaffected by this autouse fixture.

    Args:
        monkeypatch: pytest's monkeypatch fixture, injected automatically.
    """
    monkeypatch.delenv("DISPATCH_LOG_PATH", raising=False)
