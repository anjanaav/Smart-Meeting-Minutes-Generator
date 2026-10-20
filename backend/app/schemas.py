"""Data contracts and dependency-free validators.

``LLM_EXTRACTION_SCHEMA`` is the JSON Schema sent to the model (strict structured output).
``validate_minutes`` checks the final document before it is saved.
"""
from __future__ import annotations

SCHEMA_VERSION = 1

STATUSES = ("created", "bot_joining", "waiting", "joined", "capturing", "disconnected", "processing", "completed", "failed")

_INT_ARRAY = {"type": "array", "items": {"type": "integer"}}
_STR_OR_NULL = {"type": ["string", "null"]}

LLM_EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "key_points", "decisions", "action_items"],
    "properties": {
        "summary": {"type": "string"},
        "key_points": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["topic", "details", "segment_ids"],
                "properties": {"topic": {"type": "string"}, "details": {"type": "string"}, "segment_ids": _INT_ARRAY},
            },
        },
        "decisions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["description", "speaker", "context", "segment_ids"],
                "properties": {"description": {"type": "string"}, "speaker": _STR_OR_NULL, "context": {"type": "string"}, "segment_ids": _INT_ARRAY},
            },
        },
        "action_items": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["task", "assignee", "deadline", "segment_ids"],
                "properties": {"task": {"type": "string"}, "assignee": _STR_OR_NULL, "deadline": _STR_OR_NULL, "segment_ids": _INT_ARRAY},
            },
        },
    },
}

REDUCE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary"], "properties": {"summary": {"type": "string"}},
}


def _is_str(x): return isinstance(x, str)
def _is_int(x): return isinstance(x, int) and not isinstance(x, bool)
def _is_num(x): return isinstance(x, (int, float)) and not isinstance(x, bool)
def _is_str_or_none(x): return x is None or isinstance(x, str)
def _is_int_list(x): return isinstance(x, list) and all(_is_int(i) for i in x)


def _check_list(errors, doc, key, item_checks):
    value = doc.get(key)
    if not isinstance(value, list):
        errors.append(f"{key}: must be a list")
        return
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"{key}[{i}]: must be an object")
            continue
        for field, check in item_checks.items():
            if field not in item:
                errors.append(f"{key}[{i}].{field}: missing")
            elif not check(item[field]):
                errors.append(f"{key}[{i}].{field}: wrong type")


def validate_llm_extraction(obj) -> list:
    """Return a list of problems (empty if the model output matches LLM_EXTRACTION_SCHEMA)."""
    if not isinstance(obj, dict):
        return ["root: must be an object"]
    errors: list = []
    if not _is_str(obj.get("summary")):
        errors.append("summary: must be a string")
    _check_list(errors, obj, "key_points", {"topic": _is_str, "details": _is_str, "segment_ids": _is_int_list})
    _check_list(errors, obj, "decisions", {"description": _is_str, "speaker": _is_str_or_none, "context": _is_str, "segment_ids": _is_int_list})
    _check_list(errors, obj, "action_items", {"task": _is_str, "assignee": _is_str_or_none, "deadline": _is_str_or_none, "segment_ids": _is_int_list})
    return errors


def validate_minutes(doc) -> list:
    """Validate the final stored document. Returns a list of problems (empty if valid)."""
    if not isinstance(doc, dict):
        return ["root: must be an object"]
    errors: list = []
    for key, check in (
        ("meeting_id", _is_str), ("meeting_title", _is_str), ("meeting_date", _is_str),
        ("duration_seconds", _is_num), ("summary", _is_str), ("generated_by", _is_str),
    ):
        if key not in doc or not check(doc[key]):
            errors.append(f"{key}: missing or wrong type")
    if doc.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version: unsupported")
    _check_list(errors, doc, "key_points", {"topic": _is_str, "details": _is_str, "segment_ids": _is_int_list})
    _check_list(errors, doc, "decisions", {"description": _is_str, "speaker": _is_str_or_none, "context": _is_str, "evidence": _is_str, "segment_ids": _is_int_list})
    _check_list(errors, doc, "action_items", {"task": _is_str, "assignee": _is_str_or_none, "deadline": _is_str_or_none, "status": _is_str, "evidence": _is_str, "segment_ids": _is_int_list})
    _check_list(errors, doc, "participants", {"name": _is_str})
    _check_list(errors, doc, "transcript", {"id": _is_int, "speaker": _is_str, "text": _is_str, "start_time": _is_num, "end_time": _is_num})
    if not isinstance(doc.get("warnings"), list):
        errors.append("warnings: must be a list")
    return errors
