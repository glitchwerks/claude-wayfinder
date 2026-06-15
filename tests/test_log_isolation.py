"""Regression guard for issue #349 — test-session log isolation.

Proves that ``tests/conftest.py``'s autouse ``isolate_dispatch_log``
fixture has neutralized ``DISPATCH_LOG_PATH`` before any test body runs,
so full pytest runs cannot write fixture-prompt rows into the live
dispatch log (``~/.claude/state/dispatch-log.jsonl``).
"""

from __future__ import annotations

import os
from pathlib import Path  # noqa: I001

#: Canonical live log path that must never be touched during a test run.
_LIVE_LOG_PATH: Path = (
    Path.home() / ".claude" / "state" / "dispatch-log.jsonl"
)


def test_dispatch_log_path_absent_in_test_session() -> None:
    """DISPATCH_LOG_PATH must be absent (None) inside every test session.

    The autouse ``isolate_dispatch_log`` fixture in ``tests/conftest.py``
    calls ``monkeypatch.delenv("DISPATCH_LOG_PATH", raising=False)``
    before each test body runs.  This test asserts the outcome: the env
    var is ``None`` during the test, so ``_resolve_log_path()`` returns
    ``None`` and no log write occurs.

    If this test fails it means:
      - the autouse fixture was removed or disabled, OR
      - the fixture is not being discovered (conftest.py path issue).

    Either way, live-log pollution from subsequent matcher invocations is
    imminent and must be fixed before merging.
    """
    current = os.environ.get("DISPATCH_LOG_PATH")
    assert current is None, (
        "DISPATCH_LOG_PATH is set to {!r} inside a test session.  "
        "Expected None — the autouse isolate_dispatch_log fixture in "
        "tests/conftest.py should have called "
        "monkeypatch.delenv('DISPATCH_LOG_PATH', raising=False) before "
        "this test body ran.".format(current)
    )


def test_dispatch_log_path_not_pointing_at_live_log() -> None:
    """DISPATCH_LOG_PATH must not equal the live log path during tests.

    Complementary check: even if the env var is set to *some* value
    (e.g. a test harness re-set it globally), it must not be the
    canonical live path.  Writing to the live log during pytest is the
    root of issue #349.
    """
    current = os.environ.get("DISPATCH_LOG_PATH")
    live = str(_LIVE_LOG_PATH)
    assert current != live, (
        "DISPATCH_LOG_PATH equals the live log path ({!r}) inside a test "
        "session.  Any test that exercises the matcher entry point will "
        "append fixture-prompt rows to production telemetry.  Fix: ensure "
        "tests/conftest.py::isolate_dispatch_log is running correctly.".format(
            live
        )
    )
