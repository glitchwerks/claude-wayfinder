"""Entry point for ``python -m claude_wayfinder``.

Delegates to ``claude_wayfinder.cli.main`` so the package can be invoked
directly from the command line without an installed script entry point.

Usage::

    python -m claude_wayfinder demo
"""

from __future__ import annotations

import sys

from claude_wayfinder.cli import main

if __name__ == "__main__":
    sys.exit(main())
