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
"""

from spikes.domain_encoder._classifier import DomainClassifier, DomainResult
from spikes.domain_encoder._domains import DomainLabel

__all__ = ["DomainClassifier", "DomainLabel", "DomainResult"]
