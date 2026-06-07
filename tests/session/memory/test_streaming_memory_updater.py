# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.merge_op.base import FieldType, MergeOp
from openviking.session.memory.streaming_memory_updater import (
    MemoryUpdateRequest,
    StreamingMemoryUpdater,
    StreamingMemoryUpdaterConfig,
)
from openviking_cli.session.user_id import UserIdentifier


class InMemoryVikingFS:
    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})
        self.writes = []

    async def ls(self, uri: str, output: str = "original", ctx=None):
        del output, ctx
        prefix = uri.rstrip("/") + "/"
        return [
            {"name": path.removeprefix(prefix), "uri": path, "isDir": False}
            for path in sorted(self.files)
            if path.startswith(prefix) and "/" not in path.removeprefix(prefix)
        ]

    async def read_file(self, uri: str, ctx=None):
        uri = _canonical_user_uri(uri, ctx)
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri: str, content: str, ctx=None):
        uri = _canonical_user_uri(uri, ctx)
        self.files[uri] = content
        self.writes.append((uri, content, ctx))


def _canonical_user_uri(uri: str, ctx=None) -> str:
    if not uri.startswith("viking://user/memories/"):
        return uri
    user_id = getattr(getattr(ctx, "user", None), "user_id", None) or "u"
    return uri.replace("viking://user/memories/", f"viking://user/{user_id}/memories/", 1)


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user("u"), role=Role.ROOT)


def _registry() -> MemoryTypeRegistry:
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.register(
        MemoryTypeSchema(
            memory_type="cases",
            description="case memory",
            directory="viking://user/{{ user_space }}/memories/cases",
            filename_template="{{ case_name }}.md",
            operation_mode="add_only",
            fields=[
                MemoryField(
                    name="case_name",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="task_signature",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="input",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="rubric",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
            ],
        )
    )
    return registry


def _case_op(name: str) -> ResolvedOperation:
    return ResolvedOperation(
        old_memory_file_content=None,
        memory_type="cases",
        uris=[f"viking://user/u/memories/cases/{name}.md"],
        memory_fields={
            "case_name": name,
            "task_signature": f"{name} signature",
            "input": '{"summary":"case input"}',
            "rubric": '{"criteria":[{"name":"done","description":"done","required":true,"weight":1.0}]}',
        },
    )


@pytest.mark.asyncio
async def test_streaming_memory_updater_submit_applies_fast_path(monkeypatch):
    fs = InMemoryVikingFS({})
    fs.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs",
        lambda: fs,
    )
    monkeypatch.setattr(
        "openviking.session.memory.memory_updater.get_viking_fs",
        lambda: fs,
    )

    updater = StreamingMemoryUpdater(
        registry=_registry(),
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=8,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
    )
    result = await updater.submit(
        MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=[_case_op("重复预订处理")],
                delete_file_contents=[],
                errors=[],
            ),
            messages=[Message(id="m1", role="user", parts=[TextPart("处理重复预订")])],
            ctx=_ctx(),
        )
    )

    assert result.request_count == 1
    assert result.operations.upsert_operations[0].memory_type == "cases"
    assert result.apply_result.written_uris == ["viking://user/u/memories/cases/重复预订处理.md"]
    assert fs.writes
    written_uri, written_content, _ = fs.writes[0]
    assert written_uri.endswith("/memories/cases/重复预订处理.md")
    assert "重复预订处理" in written_content


@pytest.mark.asyncio
async def test_streaming_memory_updater_batches_concurrent_submits_and_filters_links(monkeypatch):
    fs = InMemoryVikingFS(
        {
            "viking://user/u/memories/events/existing.md": (
                "existing\n<!-- MEMORY_FIELDS\n"
                '{"memory_type":"events","content":"existing"}\n'
                "-->"
            )
        }
    )
    fs.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs",
        lambda: fs,
    )
    monkeypatch.setattr(
        "openviking.session.memory.memory_updater.get_viking_fs",
        lambda: fs,
    )

    updater = StreamingMemoryUpdater(
        registry=_registry(),
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=8,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
    )
    op1 = _case_op("并发案例A")
    op2 = _case_op("并发案例B")
    link = StoredLink(
        from_uri=op1.uris[0],
        to_uri="viking://user/u/memories/events/existing.md",
        link_type="related_to",
        weight=0.8,
        match_text="并发",
        description="valid link",
    )
    duplicate_link = link.model_copy(update={"weight": 0.6, "description": "short"})
    missing_link = StoredLink(
        from_uri=op2.uris[0],
        to_uri="viking://user/u/memories/events/missing.md",
        link_type="related_to",
        weight=0.9,
        match_text="缺失",
        description="invalid link",
    )

    result1, result2 = await asyncio.gather(
        updater.submit(
            MemoryUpdateRequest(
                operations=ResolvedOperations(
                    upsert_operations=[op1],
                    delete_file_contents=[],
                    errors=[],
                    resolved_links=[link, duplicate_link],
                ),
                messages=[Message(id="m1", role="user", parts=[TextPart("并发A")])],
                ctx=_ctx(),
            )
        ),
        updater.submit(
            MemoryUpdateRequest(
                operations=ResolvedOperations(
                    upsert_operations=[op2],
                    delete_file_contents=[],
                    errors=[],
                    resolved_links=[missing_link],
                ),
                messages=[Message(id="m2", role="user", parts=[TextPart("并发B")])],
                ctx=_ctx(),
            )
        ),
    )

    assert result1 is result2
    assert result1.request_count == 2
    assert len(result1.operations.upsert_operations) == 2
    assert len(result1.operations.resolved_links) == 1
    assert result1.operations.resolved_links[0].to_uri.endswith("/events/existing.md")
    assert sorted(result1.apply_result.written_uris) == sorted([op1.uris[0], op2.uris[0]])
