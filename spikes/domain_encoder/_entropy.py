"""Shannon entropy in bits for a probability distribution.

Used to derive the domain-any signal from the 5-way domain distribution:
high entropy (near log2(5) ≈ 2.322 bits) indicates the prompt is
domain-agnostic and belongs to an investigator / approach-critic /
auditor / researcher class (spec §9.2 finding 4).

Design: base-2 log throughout so entropy is in [0, log2(N)] bits and
the maximum is immediately interpretable (uniform over 5 classes = 2.322
bits ≈ "completely uninformative").
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def distribution_entropy(probs: Sequence[float]) -> float:
    """Compute Shannon entropy in bits for a probability distribution.

    Args:
        probs: Probability values summing to 1.0 (or close to it).
            Accepts lists, tuples, or numpy arrays.  Zero-probability
            entries are handled gracefully (0 * log2(0) = 0 by convention).

    Returns:
        Shannon entropy H = -sum(p * log2(p)) for p > 0, in bits.
        Returns 0.0 for a degenerate (one-hot) distribution.
    """
    entropy = 0.0
    for p in probs:
        # Cast to float to handle numpy scalar types transparently
        pf = float(p)
        if pf > 0.0:
            entropy -= pf * math.log2(pf)
    return entropy
