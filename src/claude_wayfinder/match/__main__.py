"""Entry point for ``python -m claude_wayfinder.match``.

Enables direct package execution so that both the installed
``claude-wayfinder dispatch`` CLI and test subprocess invocations
work correctly after the package split.
"""

from claude_wayfinder.match import main

main()
