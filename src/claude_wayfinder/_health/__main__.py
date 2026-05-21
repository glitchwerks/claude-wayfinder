"""Entry point for ``python -m claude_wayfinder._health``.

Delegates to :func:`claude_wayfinder._health.main` so the package can be
invoked directly via the ``-m`` flag, consistent with how
``claude_wayfinder.match`` is invoked after the Phase 2A split (#201).
"""

import sys

from claude_wayfinder._health import main

sys.exit(main())
