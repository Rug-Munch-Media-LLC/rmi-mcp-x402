import duckdb
from typing import Any


class DuckDBAnalytics:
    """Ephemeral in-memory DuckDB with read-only, no-external-access safety."""

    def __init__(self) -> None:
        self._conn = duckdb.connect()
        self._conn.execute("SET access_mode='read_only'")
        self._conn.execute("SET enable_external_access=false")

    def query(
        self,
        sql: str,
        params: list[Any] | None = None,
        max_rows: int = 1000,
    ) -> list[dict[str, Any]]:
        if params:
            result = self._conn.execute(sql, params)
        else:
            result = self._conn.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchmany(max_rows)
        return [dict(zip(columns, row)) for row in rows]

    def close(self) -> None:
        self._conn.close()
