from __future__ import annotations

import sqlite3
from typing import Any

from . import executors
from .executors import ExecutionContext, ExecutionError, SQLiteAdapter, _sql_params, _sql_statements


class ClosingSQLiteAdapter(SQLiteAdapter):
    """SQLite adapter that deterministically releases the database handle.

    ``sqlite3.Connection`` is a transaction context manager, not a resource
    closing context manager. Explicit closure is required on Windows before a
    temporary database file or its parent directory can be removed.
    """

    def execute(self, code: str, input_value: Any, context: ExecutionContext) -> Any:
        context.db_path.parent.mkdir(parents=True, exist_ok=True)
        params = _sql_params(input_value)
        results: list[dict[str, Any]] = []
        affected = 0
        connection = sqlite3.connect(context.db_path)
        try:
            connection.row_factory = sqlite3.Row
            for statement in _sql_statements(code):
                try:
                    cursor = connection.execute(statement, params)
                except sqlite3.Error as exc:
                    raise ExecutionError(
                        f"SQL 執行失敗：{exc}; statement={statement[:180]!r}"
                    ) from exc
                try:
                    if cursor.description:
                        results = [dict(row) for row in cursor.fetchall()]
                    elif cursor.rowcount and cursor.rowcount > 0:
                        affected += cursor.rowcount
                finally:
                    cursor.close()
            connection.commit()
        finally:
            connection.close()

        return {
            "database": str(context.db_path.resolve()),
            "affected_rows": affected,
            "rows": results,
        }


def install_closing_sqlite_adapter() -> None:
    """Replace the v0.2 adapter instance without changing public language names."""
    adapter = ClosingSQLiteAdapter()
    executors._ADAPTERS[adapter.language] = adapter
    for alias in adapter.aliases:
        executors._ADAPTERS[alias] = adapter
