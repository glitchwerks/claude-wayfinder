"""Package marker for bundled platform-agent sidecar fixtures.

This sub-package ships two YAML sidecar files used by ``catalog build``
(Pass 2.6) as the default builtin-agent trigger configuration when the
user has not placed their own sidecars at ``~/.claude/triggers/builtin/``:

- ``Explore.yml`` — read-only code-recon agent; routes on locate/find/grep
  keywords and the ``@Explore`` agent mention.
- ``Plan.yml`` — architecture/strategy agent; routes on strategy/design/
  architect keywords and the ``@Plan`` agent mention.

These fixtures are in-package (Issue #286) so a fresh install automatically
includes Explore and Plan in the dispatch catalog without requiring the
operator to author sidecars manually.
"""
