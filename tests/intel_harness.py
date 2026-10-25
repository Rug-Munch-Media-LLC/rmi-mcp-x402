"""Fakes for the intelligence tools, injected through the IntelDeps seam.

No monkeypatching: the tools take their collaborators as data, so these
plain objects stand in for httpx/asyncpg/duckdb without touching modules.
"""

from __future__ import annotations

from typing import Any

from data_mcp_intel_deps import IntelDeps


class FakeRPC:
    """Stands in for `data_mcp_rpc._rpc_call`, answering per JSON-RPC method."""

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[tuple[str, Any]] = []

    async def __call__(self, endpoints, method: str, params: list | None = None, ttl: int = 10):
        self.calls.append((method, params))
        return self.answers.get(
            method, {"ok": False, "error": "no answer configured", "endpoint": None}
        )


class FakePG:
    """Stands in for an asyncpg connection."""

    def __init__(self, rows: list[dict] | None = None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.closed = False

    async def fetch(self, query: str, arg: Any):
        if self.error is not None:
            raise self.error
        return self.rows

    async def close(self) -> None:
        self.closed = True


class FakeSeries:
    """The slice of a pandas Series the history summary touches."""

    def __init__(self, values: list) -> None:
        self.values = values

    def min(self):
        return min(self.values)

    def max(self):
        return max(self.values)

    def nunique(self) -> int:
        return len(set(self.values))


class FakeFrame:
    """The slice of a pandas DataFrame the history/sql summaries touch."""

    def __init__(self, records: list[dict]) -> None:
        self.records = records
        self.columns = list(records[0]) if records else []

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, column: str) -> FakeSeries:
        return FakeSeries([r[column] for r in self.records])

    def head(self, n: int) -> FakeFrame:
        return FakeFrame(self.records[:n])

    def to_dict(self, orient: str = "records") -> list[dict]:
        if orient != "records":
            raise ValueError(f"unsupported orient {orient}")
        return list(self.records)


class FakeDuck:
    """Stands in for a DuckDB connection: records SQL, returns a FakeFrame."""

    def __init__(self, records: list[dict] | None = None, error: Exception | None = None) -> None:
        self.records = records or []
        self.error = error
        self.statements: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> FakeDuck:
        self.statements.append((sql, params))
        return self

    def fetchdf(self) -> FakeFrame:
        if self.error is not None:
            raise self.error
        return FakeFrame(self.records)


def deps(
    rpc: FakeRPC | None = None,
    pg: FakePG | None = None,
    duck: FakeDuck | None = None,
    token: str = "",
) -> IntelDeps:
    """Build deps with every collaborator faked and the archive gate explicit."""

    async def get_pg():
        return pg

    return IntelDeps(
        rpc_call=rpc or FakeRPC(),
        get_pg=get_pg,
        get_duckdb=lambda: duck if duck is not None else FakeDuck(),
        hf_token=token,
    )
