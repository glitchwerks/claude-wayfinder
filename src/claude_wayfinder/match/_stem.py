"""Porter2 / Snowball stemming helper for the dispatch matcher.

Wraps ``snowballstemmer`` to provide a single ``stem()`` function used
symmetrically on both the catalog side (at build time) and the feature
side (at extraction time).  Symmetric normalization is load-bearing: the
same stemmer instance and language must be used on both sides or morphological
variants will not collapse to the same form.

Concurrency: the ``SnowballStemmer`` instance is module-level and is
created once.  The stemmer object is stateless between calls, so sharing
it across threads is safe.
"""

from __future__ import annotations

import snowballstemmer

# ---------------------------------------------------------------------------
# Module-level stemmer instance
# ---------------------------------------------------------------------------

#: Singleton English (Porter2) stemmer; created once at module import time.
#: The snowballstemmer package exposes per-language classes; use
#: ``EnglishStemmer`` directly rather than a generic factory.
_STEMMER: snowballstemmer.EnglishStemmer = snowballstemmer.EnglishStemmer()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def stem(word: str, *, no_stem: bool = False) -> str:
    """Return the Porter2 stem of *word*, or *word* verbatim when opted out.

    The function lowercases *word* before stemming so that callers do not
    need to normalise case themselves.  An empty string is returned
    unchanged (the stemmer otherwise raises on empty input on some builds).

    Hyphens and other non-alpha characters are preserved; the stemmer
    operates on the full token as supplied.  Tokens such as ``"git-rebase"``
    pass through without splitting at the hyphen boundary.

    Args:
        word: A single token to stem.  Need not be pre-lowercased; this
            function lowercases before stemming.
        no_stem: When ``True`` the token is returned verbatim (lowercased
            but not stemmed).  Use this for acronyms and product names
            that must not collapse — e.g. ``aws``, ``gh``, ``ps1``.

    Returns:
        The Porter2 stem of *word* (lowercased), or the lowercased input
        unchanged when *no_stem* is ``True`` or *word* is empty.

    Examples:
        >>> stem("implementing")
        'implement'
        >>> stem("refactored")
        'refactor'
        >>> stem("aws", no_stem=True)
        'aws'
        >>> stem("")
        ''
    """
    lowered = word.lower()
    if not lowered or no_stem:
        return lowered
    return _STEMMER.stemWord(lowered)
