"""Domain-encoder spike — potion-base-8M, 5-way domain + entropy.

Issue: glitchwerks/claude-wayfinder#329
Milestone 14 — Matcher v3 semantic two-axis.

Public surface: DomainClassifier, DomainResult, DomainLabel.

Usage::

    from spikes.domain_encoder import DomainClassifier
    clf = DomainClassifier.from_pretrained("minishlab/potion-base-8M")
    result = clf.classify("Fix the failing test in test_api.py")
    print(result.top_label, result.entropy, result.distribution)

Requires the ``spike`` optional extra::

    pip install ".[spike]"

Implementation note — lazy imports (PEP 562):
    The heavy modules (_classifier → _head → numpy, model2vec) are NOT
    imported eagerly at package-init time.  This keeps ``import
    spikes.domain_encoder._paths`` numpy-free so the 13 path/repo-id
    tests in ``tests/test_domain_encoder/test_paths.py`` can run on CI
    (which installs only ``.[dev]``, not ``.[spike]``).  Consumers that
    need the public API (``DomainClassifier``, ``DomainResult``,
    ``DomainLabel``) trigger the heavy imports on first attribute access
    via ``__getattr__``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spikes.domain_encoder._classifier import DomainClassifier, DomainResult
    from spikes.domain_encoder._domains import DomainLabel

__all__ = ["DomainClassifier", "DomainLabel", "DomainResult"]

# Mapping of public name → (module path, attribute name).
# Populated lazily by __getattr__ so the heavy imports never run during
# package initialization — only when a caller actually accesses the name.
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "DomainClassifier": ("spikes.domain_encoder._classifier", "DomainClassifier"),
    "DomainResult": ("spikes.domain_encoder._classifier", "DomainResult"),
    "DomainLabel": ("spikes.domain_encoder._domains", "DomainLabel"),
}


def __getattr__(name: str) -> object:
    """PEP 562 lazy attribute loader for heavy spike exports.

    Args:
        name: The attribute being accessed on the package.

    Returns:
        The requested class/object after importing its source module.

    Raises:
        AttributeError: If *name* is not in the public surface.
    """
    if name in _LAZY_IMPORTS:
        module_path, attr = _LAZY_IMPORTS[name]
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
