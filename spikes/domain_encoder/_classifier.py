"""DomainClassifier — public API for the domain-encoder spike.

Wraps model2vec StaticModel + CentroidHead into a single deterministic
classify() call.  The classifier is entirely offline after first model
load; no network calls at inference time.

Usage::

    clf = DomainClassifier.from_pretrained("minishlab/potion-base-8M")
    result = clf.classify("Fix the bug in the auth module")
    print(result.top_label)      # e.g. "code"
    print(result.entropy)        # Shannon entropy in bits
    print(result.distribution)   # {"code": 0.72, "infra_deploy": 0.05, ...}

CLI::

    python -m spikes.domain_encoder "Fix the bug in the auth module"

Model: minishlab/potion-base-8M
Model revision: bf8b056651a2c21b8d2565580b8569da283cab23 (pinned — see §7)
    The default loader pins this exact commit via huggingface_hub.snapshot_download
    so centroid numbers are reproducible regardless of upstream pushes.
Model2Vec version: 0.8.2
Seed phrases version: 2026-06-11-v1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from spikes.domain_encoder._domains import DOMAIN_CLASSES, DomainLabel
from spikes.domain_encoder._entropy import distribution_entropy
from spikes.domain_encoder._head import CentroidHead
from spikes.domain_encoder._paths import _is_hf_repo_id

if TYPE_CHECKING:
    import numpy as np

# Default model identifier and the exact git commit it resolves to.
# Revision pinned so that centroid numbers match the spike report §7
# regardless of future upstream pushes to the model repo.  verified 2026-06-11
DEFAULT_MODEL_NAME = "minishlab/potion-base-8M"
DEFAULT_MODEL_REVISION = "bf8b056651a2c21b8d2565580b8569da283cab23"  # verified 2026-06-11


@dataclass
class DomainResult:
    """Result of a domain classification.

    Attributes:
        distribution: Mapping of DomainLabel value → probability (sums to ~1.0).
            Keys are the string values of DomainLabel (e.g. ``"code"``,
            ``"infra_deploy"``).
        top_label: String value of the highest-probability domain class.
        entropy: Shannon entropy of the distribution in bits.
            Range: [0.0, log2(5)] ≈ [0, 2.322].
            High entropy (> ~1.5) indicates a domain-agnostic prompt —
            the domain-any signal per spec §9.2 finding 4.
    """

    distribution: dict[str, float]
    top_label: str
    entropy: float


class DomainClassifier:
    """Deterministic 5-way domain classifier over frozen embeddings.

    Classification head: centroid nearest-prototype (see _head.py for
    the design rationale and why this guarantees determinism).

    The classifier is stateless after construction — no mutable state is
    modified during classify().  Thread-safe for concurrent read access.

    Attributes:
        domain_classes: Tuple of DomainLabel values in internal index order.
    """

    domain_classes: tuple[DomainLabel, ...] = DOMAIN_CLASSES

    def __init__(self, model: object, head: CentroidHead) -> None:
        """Initialise from a pre-loaded model and pre-built head.

        Args:
            model: model2vec StaticModel (or any object with
                ``encode(sentences) -> np.ndarray``).
            head: Pre-built CentroidHead with frozen centroids.
        """
        self._model = model
        self._head = head

    @classmethod
    def from_pretrained(
        cls,
        model_name_or_path: str = DEFAULT_MODEL_NAME,
        revision: str | None = DEFAULT_MODEL_REVISION,
    ) -> "DomainClassifier":
        """Load the model and build the centroid head.

        When ``model_name_or_path`` is a HuggingFace repo id (not a local
        path) and ``revision`` is provided, the model is loaded from the
        exact commit snapshot via ``huggingface_hub.snapshot_download`` so
        that centroid numbers are reproducible across upstream repo pushes.

        Pass ``revision=None`` to disable pinning (e.g. for exploratory runs
        with a different model that has no known-good revision yet).

        Args:
            model_name_or_path: HuggingFace model id or local path.
                Defaults to ``DEFAULT_MODEL_NAME``
                (``"minishlab/potion-base-8M"``).
            revision: Exact git commit SHA to load.  Defaults to
                ``DEFAULT_MODEL_REVISION`` (the revision whose centroid
                numbers match the spike report §7).  Pass ``None`` to load
                the latest version of the model (mutable — not recommended
                for reproducibility).

        Returns:
            Fully initialised DomainClassifier ready for inference.

        Raises:
            ImportError: If model2vec is not installed.  Install with
                ``pip install ".[spike]"``.
        """
        try:
            from model2vec import StaticModel  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "model2vec is required for the domain-encoder spike. "
                "Install with: pip install '.[spike]'"
            ) from exc

        load_path: str = model_name_or_path
        # Only pin via snapshot_download when the caller passed a HF repo id
        # and a revision is requested.  _is_hf_repo_id is platform-independent:
        # it never reads os.path.sep / os.sep so "minishlab/potion-base-8M"
        # is correctly recognised as a repo id on both POSIX and Windows.
        if revision is not None and _is_hf_repo_id(model_name_or_path):
            from huggingface_hub import snapshot_download  # type: ignore[import-untyped]
            from huggingface_hub.errors import (  # type: ignore[import-untyped]
                LocalEntryNotFoundError,
            )

            # Cache-first, pinned-download fallback:
            #   1. Try to serve from the local HF cache (offline, no network).
            #   2. On cache miss (LocalEntryNotFoundError), fetch the same
            #      pinned revision from the Hub.  The resulting artifact is
            #      identical either way — only the transport differs.
            try:
                load_path = snapshot_download(
                    repo_id=model_name_or_path,
                    revision=revision,
                    local_files_only=True,
                )
            except LocalEntryNotFoundError:
                load_path = snapshot_download(
                    repo_id=model_name_or_path,
                    revision=revision,
                )

        model = StaticModel.from_pretrained(load_path)
        head = CentroidHead.build(model)
        return cls(model, head)

    def _encode_single(self, text: str) -> "np.ndarray":
        """Encode a single text to a 1-D embedding.

        Args:
            text: Input text to encode.

        Returns:
            1-D float32 numpy array of shape (dim,).
        """
        # encode() returns (1, dim); squeeze to (dim,)
        emb = self._model.encode([text], show_progress_bar=False)  # type: ignore[union-attr]
        return emb[0]

    def classify(self, text: str) -> DomainResult:
        """Classify a single text into the 5-way domain distribution.

        Deterministic: given identical input, output is bit-identical
        across repeated calls and across separate process invocations
        (frozen model weights + frozen seed phrases + deterministic numpy).

        Args:
            text: Task description to classify.

        Returns:
            DomainResult with distribution, top_label, and entropy.
        """
        embedding = self._encode_single(text)
        dist_by_label = self._head.predict_distribution(embedding)

        # Convert DomainLabel keys to string values for the public API
        dist_str: dict[str, float] = {label.value: prob for label, prob in dist_by_label.items()}

        entropy = distribution_entropy(list(dist_str.values()))
        top_label = max(dist_str, key=dist_str.__getitem__)

        return DomainResult(distribution=dist_str, top_label=top_label, entropy=entropy)

    def classify_batch(self, texts: list[str]) -> list[DomainResult]:
        """Classify a batch of texts.

        Args:
            texts: List of task descriptions to classify.

        Returns:
            List of DomainResult in the same order as the input texts.
        """
        if not texts:
            return []

        embeddings = self._model.encode(texts, show_progress_bar=False)  # type: ignore[union-attr]
        results: list[DomainResult] = []
        for emb in embeddings:
            dist_by_label = self._head.predict_distribution(emb)
            dist_str = {label.value: prob for label, prob in dist_by_label.items()}
            entropy = distribution_entropy(list(dist_str.values()))
            top_label = max(dist_str, key=dist_str.__getitem__)
            results.append(
                DomainResult(distribution=dist_str, top_label=top_label, entropy=entropy)
            )

        return results
