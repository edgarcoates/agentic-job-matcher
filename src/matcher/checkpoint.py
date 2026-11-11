"""Saved progress for resumable runs.

Every live or practice run saves its graph state after each step to runs/<run_id>/checkpoint.sqlite (LangGraph's
SQLite checkpointer). If a run stops part-way (network drop, API error), `python -m matcher resume <run_id>` continues
from the last completed step. Inside a parallel search round, lane searches that finished are kept, and only the
failed ones run again, so completed (paid-for) work is never repeated.
"""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel

from . import schemas


def _schema_classes() -> list[tuple[str, str]]:
    """Only this project's own data classes may be loaded back from a checkpoint."""
    return [(schemas.__name__, name) for name, obj in inspect.getmembers(schemas, inspect.isclass)
            if issubclass(obj, BaseModel) and obj.__module__ == schemas.__name__]


def open_checkpointer(path: Path) -> SqliteSaver:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)  # lane searches run in parallel threads
    return SqliteSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=_schema_classes()))
