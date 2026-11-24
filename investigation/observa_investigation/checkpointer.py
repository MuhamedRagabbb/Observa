"""
Durable SQLite Checkpointer for LangGraph (F2-4).
Persists investigation StateGraph checkpoints across process restarts.

Serialization note: LangGraph's serializer returns a (type_tag, bytes) pair and the tag
depends on the installed langgraph version (json or msgpack). The checkpoint AND the
metadata therefore both store their own tag; hard-coding "json" when reading broke resume().
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Sequence, Union

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langchain_core.runnables import RunnableConfig

_COLUMNS = (
    "thread_id, checkpoint_ns, checkpoint_id, parent_id, type, checkpoint, metadata, metadata_type"
)


class SqliteSaver(BaseCheckpointSaver):
    """Durable SQLite checkpoint saver supporting process restarts and resume()."""

    def __init__(self, db_path: Union[str, Path] = "investigation_checkpoints.db"):
        super().__init__()
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        with self._lock, self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT,
                    checkpoint_ns TEXT,
                    checkpoint_id TEXT,
                    parent_id TEXT,
                    type TEXT,
                    checkpoint BLOB,
                    metadata BLOB,
                    metadata_type TEXT,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                )
                """
            )
            # A database created by the first version has no metadata_type column.
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(checkpoints)")}
            if "metadata_type" not in cols:
                self.conn.execute("ALTER TABLE checkpoints ADD COLUMN metadata_type TEXT")
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS writes (
                    thread_id TEXT,
                    checkpoint_ns TEXT,
                    checkpoint_id TEXT,
                    task_id TEXT,
                    idx INTEGER,
                    channel TEXT,
                    type TEXT,
                    value BLOB,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
                )
                """
            )

    def _row_to_tuple(self, row) -> CheckpointTuple:
        cp = self.serde.loads_typed((row[4], row[5]))
        # Rows written before metadata_type existed (NULL) were serialized by the same
        # serializer as their checkpoint, so the checkpoint's tag is the best fallback.
        meta = self.serde.loads_typed((row[7] or row[4], row[6])) if row[6] else {}
        parent_config = (
            {
                "configurable": {
                    "thread_id": row[0],
                    "checkpoint_ns": row[1],
                    "checkpoint_id": row[3],
                }
            }
            if row[3]
            else None
        )
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": row[0],
                    "checkpoint_ns": row[1],
                    "checkpoint_id": row[2],
                }
            },
            checkpoint=cp,
            metadata=meta,
            parent_config=parent_config,
        )

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"].get("checkpoint_id")
        with self._lock, self.conn:
            if checkpoint_id:
                cur = self.conn.execute(
                    f"SELECT {_COLUMNS} FROM checkpoints "
                    "WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                    (thread_id, checkpoint_ns, checkpoint_id),
                )
            else:
                cur = self.conn.execute(
                    f"SELECT {_COLUMNS} FROM checkpoints "
                    "WHERE thread_id = ? AND checkpoint_ns = ? "
                    "ORDER BY checkpoint_id DESC LIMIT 1",
                    (thread_id, checkpoint_ns),
                )
            row = cur.fetchone()
        return self._row_to_tuple(row) if row else None

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        if not config:
            return iter([])
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        query = f"SELECT {_COLUMNS} FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ?"
        params: list = [thread_id, checkpoint_ns]
        if before:
            query += " AND checkpoint_id < ?"
            params.append(before["configurable"]["checkpoint_id"])
        query += " ORDER BY checkpoint_id DESC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)

        with self._lock, self.conn:
            rows = self.conn.execute(query, params).fetchall()
        return iter([self._row_to_tuple(row) for row in rows])

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_id = config["configurable"].get("checkpoint_id")
        t, data = self.serde.dumps_typed(checkpoint)
        mt, mdata = self.serde.dumps_typed(metadata) if metadata else (None, b"")
        with self._lock, self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints
                (thread_id, checkpoint_ns, checkpoint_id, parent_id, type, checkpoint, metadata, metadata_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (thread_id, checkpoint_ns, checkpoint_id, parent_id, t, data, mdata, mt),
            )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        with self._lock, self.conn:
            for idx, (channel, val) in enumerate(writes):
                t, data = self.serde.dumps_typed(val)
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO writes
                    (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, t, data),
                )

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self.conn.close()
