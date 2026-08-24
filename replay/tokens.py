"""Token counting for trace budgeting.

Uses tiktoken's o200k_base encoding with a cached encoder.  If tiktoken is
unavailable or the encoding cannot be loaded (e.g. its BPE file would need a
network download and we are offline), falls back to len(text) // 4 -- the
fallback is deterministic and errs on the generous side for plain ASCII.
"""
from __future__ import annotations

_ENCODER = None
_ENCODER_FAILED = False


def _get_encoder():
    global _ENCODER, _ENCODER_FAILED
    if _ENCODER is not None or _ENCODER_FAILED:
        return _ENCODER
    try:
        import tiktoken

        _ENCODER = tiktoken.get_encoding("o200k_base")
    except Exception:
        # ImportError, network/download failure, cache corruption, ...
        _ENCODER_FAILED = True
        _ENCODER = None
    return _ENCODER


def count_tokens(text: str) -> int:
    """Count tokens in ``text`` (o200k_base; fallback len(text)//4)."""
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text, disallowed_special=()))
        except Exception:
            pass
    return len(text) // 4
