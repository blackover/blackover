"""Yerel kayıt defteri (SQLite).

Uygulamadan yapılan her servis çağrısı burada saklanır: ne gönderildi, servis
ne yanıtladı, ne kadar sürdü. Böylece EPDK'ya gönderilen verinin izlenebilir
bir kaydı oluşur ve hata durumunda geriye dönük inceleme yapılabilir.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    environment TEXT,
    username    TEXT,
    table_key   TEXT,
    action      TEXT    NOT NULL,
    endpoint    TEXT,
    success     INTEGER NOT NULL,
    status      INTEGER,
    message     TEXT,
    record_id   TEXT,
    request     TEXT,
    response    TEXT,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS activity_ts_idx ON activity (ts DESC);
CREATE INDEX IF NOT EXISTS activity_table_idx ON activity (table_key, ts DESC);
"""


class ActivityStore:
    """İş parçacığı güvenli, küçük bir kayıt deposu."""

    def __init__(self, path: Path, limit: int = 2000):
        self.path = Path(path)
        self.limit = limit
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._connection.commit()

    # ------------------------------------------------------------------
    def record(
        self,
        *,
        action: str,
        success: bool,
        environment: str = "",
        username: str = "",
        table_key: str = "",
        endpoint: str = "",
        status: Optional[int] = None,
        message: Optional[str] = None,
        record_id: Optional[str] = None,
        request: Any = None,
        response: Any = None,
        duration_ms: Optional[int] = None,
    ) -> int:
        payload = _safe_json(_mask_secrets(request))
        answer = _safe_json(response)
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO activity (ts, environment, username, table_key, action,
                                      endpoint, success, status, message, record_id,
                                      request, response, duration_ms)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    environment,
                    username,
                    table_key,
                    action,
                    endpoint,
                    1 if success else 0,
                    status,
                    (message or "")[:2000],
                    record_id,
                    payload,
                    answer,
                    duration_ms,
                ),
            )
            self._connection.execute(
                """
                DELETE FROM activity
                 WHERE id <= (SELECT MAX(id) - ? FROM activity)
                """,
                (self.limit,),
            )
            self._connection.commit()
            return int(cursor.lastrowid or 0)

    def list(
        self,
        *,
        limit: int = 200,
        table_key: str = "",
        only_failures: bool = False,
        search: str = "",
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM activity WHERE 1=1"
        params: List[Any] = []
        if table_key:
            query += " AND table_key = ?"
            params.append(table_key)
        if only_failures:
            query += " AND success = 0"
        if search:
            query += " AND (message LIKE ? OR request LIKE ? OR action LIKE ?)"
            needle = f"%{search}%"
            params.extend([needle, needle, needle])
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 5000)))

        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [_row_to_dict(row) for row in rows]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._connection.execute(
                "SELECT COUNT(*) FROM activity"
            ).fetchone()[0]
            failures = self._connection.execute(
                "SELECT COUNT(*) FROM activity WHERE success = 0"
            ).fetchone()[0]
            today = self._connection.execute(
                "SELECT COUNT(*) FROM activity WHERE ts LIKE ?",
                (f"{datetime.now().date().isoformat()}%",),
            ).fetchone()[0]
        return {"total": total, "failures": failures, "today": today}

    def clear(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM activity")
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["success"] = bool(item["success"])
    for key in ("request", "response"):
        if item.get(key):
            try:
                item[key] = json.loads(item[key])
            except ValueError:
                pass
    return item


def _mask_secrets(payload: Any) -> Any:
    """Parola gibi alanların kayıt defterine yazılmasını engeller."""
    if isinstance(payload, dict):
        return {
            key: ("••••••" if key.lower() in {"password", "sifre", "parola"} else _mask_secrets(value))
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_mask_secrets(item) for item in payload]
    return payload


def _safe_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:20000]
    except (TypeError, ValueError):
        return str(value)[:20000]
