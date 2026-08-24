"""LLM interface package: schema, client, prompts, and repair helper."""

from .client import LunaClient, ModelCall
from .prompts import (
    build_reflection_prompt,
    build_repair_prompt,
    build_score_only_prompt,
    load_rules_digest,
)
from .repair import build_repair_call
from .schemas import REFLECT_AND_PATCH_SCHEMA, SchemaError, parse_and_validate

__all__ = [
    "LunaClient",
    "ModelCall",
    "build_reflection_prompt",
    "build_score_only_prompt",
    "build_repair_prompt",
    "build_repair_call",
    "load_rules_digest",
    "parse_and_validate",
    "SchemaError",
    "REFLECT_AND_PATCH_SCHEMA",
]
