"""JSONB read helper: normalize values coming out of psycopg3.

psycopg3 maps JSONB columns to Python dict/list automatically. Code paths
written against the old SQLite schema stored JSON as serialized TEXT and used
`json.loads()` to decode. Both formats are now legitimate: SQLAlchemy-style
JSON serialized text rows can still exist if a migration wrote them that way.
This helper picks the right form without forcing every reader to know."""
import json


def maybe_jsonb(v):
    """Return dict/list when JSONB, decoded dict/list when legacy TEXT JSON,
    or v unchanged for everything else (None, int, ...). Never raises."""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, (bytes, bytearray)):
        try:
            return json.loads(v.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return v
    return v
