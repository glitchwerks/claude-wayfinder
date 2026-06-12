"""Corpus eval harness — phase C (issue #340).

Offline evaluation of four matcher systems against the phase-A corpus
format.  Produces a six-metric table per spec §13.3.

Modules
-------
_reader    Corpus JSONL reader + optional gold-label join.
_systems   Four system runners (lexical, encoder, extractors, composed).
_metrics   Six metric computations.
__main__   CLI: corpus path + optional labels + catalog → metrics table.

Usage (one command per §13 run)::

    python -m scripts.corpus.eval \\
        --corpus PATH \\
        --labels PATH \\
        --catalog PATH \\
        [--systems all|lexical|encoder|extractors|composed]

CI note: encoder-dependent paths (system 2 and composed) follow the
importorskip pattern; CI installs ``.[dev]`` only (no spike extras) and
stays green.  Pass ``--systems lexical,extractors`` to skip encoder
paths explicitly.
"""
