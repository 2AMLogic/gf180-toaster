"""Strict JSON decoding for the harness's file inputs.

GENERIC CORE. The stdlib decoder collapses an object that declares the same
key twice down to the last value, *before* any schema validation runs: a
manifest visibly stating ``"max": 0.5`` and later ``"max": 5`` validates
against 5 alone, and a pin file can silently discard the stricter of two
version floors. The file text is ambiguous, so the loaders refuse it rather
than guess: every object key must be unique, at every nesting level, even
when the two values are identical (accepting ambiguous text would make a
later edit of one copy a silent behaviour change), and regardless of
spelling -- ``"max"`` and ``"\\u006dax"`` decode to the same key and collide
like any other duplicate. Programmatically supplied dictionaries cannot
represent repeated keys and need no such check; this hook exists for the
file-decoding boundary (``tb.json``, ``toolchain.json``).
"""

from __future__ import annotations


def no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    """``json.loads`` ``object_pairs_hook`` refusing a repeated object key.

    Raises ``ValueError`` naming the duplicate so each loader re-raises it
    in its own public error type with the input file prefixed.
    """
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON object key {key!r}")
        obj[key] = value
    return obj
