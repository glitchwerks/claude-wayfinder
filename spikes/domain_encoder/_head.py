"""Deterministic centroid-based classification head.

Classification head design: nearest-prototype (centroid) over frozen embeddings.

Choice rationale (spec §AC "document the choice and why"):
- **Centroid / nearest-prototype** was explicitly listed as the canonical
  deterministic head in the acceptance criteria.  Given frozen embeddings
  from a static model (potion-base-8M), a centroid is a fixed vector
  computed once from the seed phrases at load time.  At classify time,
  the query embedding is compared to each centroid via cosine similarity,
  and the softmax of similarities is the distribution.
- **Determinism**: fully guaranteed.  The seed phrase list is a frozen
  constant (SEED_PHRASES_VERSION), the model weights are frozen, numpy
  float32 arithmetic is bit-identical across calls given the same input.
  No stochastic component anywhere in the pipeline.
- **Alternatives considered**: (a) MLP head — requires training data not
  available offline; (b) BM25/keyword lookup — not an embedding approach;
  (c) cosine raw scores vs softmax — raw scores are not a distribution
  (do not sum to 1), so softmax is required for the entropy signal.
- **Softmax temperature**: T=1.0 (standard).  Lowering T sharpens the
  distribution (lower entropy at classification time), raising T flattens
  it.  T=1.0 is chosen to preserve the model's natural similarity spread,
  so entropy values are intrinsic to the model rather than post-hoc tuned.

Source refs: spec §8.2, §9.3, acceptance criteria.
"""

from __future__ import annotations

import math

import numpy as np

from spikes.domain_encoder._domains import DOMAIN_CLASSES, SEED_PHRASES, DomainLabel


class CentroidHead:
    """Frozen centroid-based classification head.

    Each class centroid is the mean of the L2-normalised embeddings of
    its seed phrases.  Classification is cosine similarity → softmax.

    Attributes:
        centroids: Float32 array of shape (n_classes, dim) — the per-class
            centroid embeddings, L2-normalised.
        class_order: Tuple of DomainLabel in the same order as centroids rows.
    """

    def __init__(self, centroids: np.ndarray, class_order: tuple[DomainLabel, ...]) -> None:
        """Initialise from pre-computed centroid matrix.

        Args:
            centroids: (n_classes, dim) float32 array, rows L2-normalised.
            class_order: Domain labels in the same row order as centroids.
        """
        self.centroids = centroids
        self.class_order = class_order

    @classmethod
    def build(cls, encoder: object) -> "CentroidHead":
        """Build centroids from the frozen seed phrases via the encoder.

        Args:
            encoder: Any object with an ``encode(sentences) -> np.ndarray``
                method returning (n, dim) float32 embeddings.  Intended to
                be the model2vec StaticModel.

        Returns:
            Initialised CentroidHead with frozen centroids.
        """
        centroid_rows: list[np.ndarray] = []
        for label in DOMAIN_CLASSES:
            phrases = list(SEED_PHRASES[label])
            # encode returns (n, dim) float32
            embeddings = encoder.encode(phrases, show_progress_bar=False)  # type: ignore[union-attr]
            # Normalise each phrase embedding before averaging
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            # Guard against zero vectors (degenerate phrases)
            norms = np.where(norms == 0, 1.0, norms)
            normed = embeddings / norms
            # Centroid = mean of normalised phrase embeddings
            centroid = normed.mean(axis=0)
            # Normalise the centroid itself so cosine sim = dot product
            c_norm = np.linalg.norm(centroid)
            if c_norm > 0:
                centroid = centroid / c_norm
            centroid_rows.append(centroid.astype(np.float32))

        centroids = np.stack(centroid_rows, axis=0)  # (n_classes, dim)
        return cls(centroids, DOMAIN_CLASSES)

    def predict_distribution(self, query_embedding: np.ndarray) -> dict[DomainLabel, float]:
        """Compute the 5-way domain distribution for a single query embedding.

        Args:
            query_embedding: (dim,) float32 embedding vector.

        Returns:
            Dict mapping DomainLabel → probability, summing to 1.0.
        """
        # L2-normalise the query
        norm = np.linalg.norm(query_embedding)
        if norm > 0:
            q = query_embedding / norm
        else:
            q = query_embedding

        # Cosine similarities: (n_classes,) because centroids are normalised
        sims = self.centroids @ q.astype(np.float32)  # dot product of normalised vecs

        # Softmax over similarities (temperature=1.0)
        # Subtract max for numerical stability
        shifted = sims - sims.max()
        exp_sims = np.exp(shifted)
        probs = exp_sims / exp_sims.sum()

        return {label: float(probs[i]) for i, label in enumerate(self.class_order)}

    def predict_top1(self, query_embedding: np.ndarray) -> DomainLabel:
        """Return the top-1 domain label for a query embedding.

        Args:
            query_embedding: (dim,) float32 embedding vector.

        Returns:
            The DomainLabel with the highest cosine similarity to the query.
        """
        dist = self.predict_distribution(query_embedding)
        return max(dist, key=dist.__getitem__)


def max_entropy_bits(n_classes: int) -> float:
    """Return the maximum possible entropy in bits for n_classes.

    Args:
        n_classes: Number of classes in the distribution.

    Returns:
        log2(n_classes) bits.
    """
    return math.log2(n_classes)
