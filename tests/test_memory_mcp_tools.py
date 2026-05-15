"""Tests for the 3 new memory MCP tools."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh_memory():
    from mcp_server import server

    server.memory_adapter._backend = server.memory_adapter._backend.__class__()
    yield


def test_finops_memory_write_persists() -> None:
    from mcp_server import server

    res = server.finops_memory_write(user_id="alice", content="prefers eu-west-2")
    assert res["status"] == "stored"
    assert res["memory_id"]


def test_finops_memory_write_blocks_secret() -> None:
    from mcp_server import server

    res = server.finops_memory_write(user_id="alice", content="key: AKIAIOSFODNN7EXAMPLE")
    assert res["status"] == "blocked"


def test_finops_memory_read_returns_user_records() -> None:
    from mcp_server import server

    server.finops_memory_write(user_id="alice", content="prefers eu-west-2")
    server.finops_memory_write(user_id="alice", content="cost-centre = CC-101")
    res = server.finops_memory_read(user_id="alice")
    assert res["status"] == "ok"
    assert res["count"] == 2


def test_finops_memory_read_is_user_scoped() -> None:
    from mcp_server import server

    server.finops_memory_write(user_id="alice", content="alice")
    server.finops_memory_write(user_id="bob", content="bob")
    res = server.finops_memory_read(user_id="alice")
    assert res["count"] == 1
    # Make sure bob's data isn't leaked.
    assert all(r["user_id"] == "alice" for r in res["records"])


def test_finops_memory_forget_user_removes_all() -> None:
    from mcp_server import server

    for _ in range(3):
        server.finops_memory_write(user_id="alice", content="x")
    res = server.finops_memory_forget_user(user_id="alice", actor="compliance")
    assert res["status"] == "completed"
    assert res["records_deleted"] == 3
    follow_up = server.finops_memory_read(user_id="alice")
    assert follow_up["count"] == 0
