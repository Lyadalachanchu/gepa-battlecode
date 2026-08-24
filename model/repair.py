"""One-attempt compile-repair flow helper (PLAN.md section 8).

Given a failed compile (static-validator or javac output) and the prior
:class:`~model.client.ModelCall`, produce the (system, user) inputs for the
single repair call.  Executing the repair is the caller's job — it counts as
one model call against the run budget, so this module only builds the inputs.
"""

from __future__ import annotations

import json
from typing import Any

from .client import ModelCall
from .prompts import build_repair_prompt

__all__ = ["build_repair_call"]


def _prior_output_dict(prior: ModelCall) -> dict[str, Any]:
    """Best-effort reconstruction of the prior attempt's output as a dict."""
    if prior.parsed is not None:
        return prior.parsed
    # Parsed failed (schema error or transport failure): try raw JSON, else
    # wrap the raw text so the model still sees what it produced.
    try:
        obj = json.loads(prior.raw_text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError):
        pass
    return {"raw_output": prior.raw_text, "note": "previous output was not valid schema JSON"}


def build_repair_call(prior: ModelCall, compiler_output: str) -> tuple[str, str]:
    """Return (system_prompt, user_content) for the one repair attempt.

    ``compiler_output`` is the raw validator or javac output from the failed
    compile.  No new match information is included, per PLAN.md section 8.
    Pass the result to ``LunaClient.reflect_and_patch(system, user)``.
    """
    return build_repair_prompt(_prior_output_dict(prior), compiler_output)
