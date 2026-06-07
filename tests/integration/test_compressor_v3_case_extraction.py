# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Integration coverage for V3 case-memory extraction from session dialogue."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openviking import AsyncOpenViking
from openviking.message import TextPart
from openviking.service.task_tracker import TaskStatus, get_task_tracker, reset_task_tracker
from openviking.session.compressor_v3 import SessionCompressorV3
from openviking.session.memory.dataclass import ResolvedOperation, ResolvedOperations
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import create_default_registry
from openviking.session.memory.memory_updater import ExtractContext, MemoryUpdater
from openviking.session.train import StreamingPolicyTrainerConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


async def _wait_for_task(task_id: str, timeout: float = 10.0) -> dict:
    """Poll the task tracker until a background session commit finishes."""
    tracker = get_task_tracker()
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        task = await tracker.get(task_id)
        if task and task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return task.to_dict()
        await asyncio.sleep(0.05)
    raise TimeoutError(f"Task {task_id} did not finish within {timeout}s")


@pytest_asyncio.fixture(scope="function")
async def v3_case_client(tmp_path, monkeypatch):
    """Create an embedded client configured to use SessionCompressorV3."""
    reset_task_tracker()
    await AsyncOpenViking.reset()
    OpenVikingConfigSingleton.reset_instance()

    workspace = tmp_path / "ov_v3_case_extraction"
    monkeypatch.setattr(
        "openviking.core.directories.DirectoryInitializer._ensure_directory_l0_l1_vectors",
        AsyncMock(return_value=None),
    )
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "storage": {
                "workspace": str(workspace),
                "skip_process_lock": True,
                "agfs": {"backend": "local"},
                "vectordb": {
                    "backend": "local",
                    "name": "test",
                    "project": "default",
                    "dimension": 512,
                },
            },
            "memory": {
                "version": "v3",
                "agent_memory_enabled": False,
                "session_skill_extraction_enabled": False,
            },
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "text-embedding-3-small",
                    "api_key": "fake-key",
                    "api_base": "http://127.0.0.1:9/v1",
                    "dimension": 512,
                }
            },
        }
    )

    client = AsyncOpenViking(path=str(workspace))
    await client.initialize()
    try:
        yield client
    finally:
        tracker = get_task_tracker()
        for _ in range(100):
            pending = [
                task
                for task in await tracker.list_tasks()
                if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
            ]
            if not pending:
                break
            await asyncio.sleep(0.05)
        await client.close()
        await AsyncOpenViking.reset()
        reset_task_tracker()
        OpenVikingConfigSingleton.reset_instance()


@pytest.mark.asyncio
async def test_add_dialogue_commit_triggers_v3_case_extraction(v3_case_client, monkeypatch):
    """Adding a concrete task dialogue and committing should extract/train a cases memory."""
    client = v3_case_client
    extracted_messages = []
    trained_cases = []

    case_operation = ResolvedOperation(
        old_memory_file_content=None,
        memory_type="cases",
        uris=["viking://user/default/memories/cases/重复预订处理.md"],
        memory_fields={
            "case_name": "重复预订处理",
            "task_signature": "处理重复预订并只取消确认重复的订单",
            "input": json.dumps(
                {
                    "summary": "用户要求处理重复预订并保留有效订单",
                    "preconditions": ["存在两个相似预订候选"],
                },
                ensure_ascii=False,
            ),
            "rubric": json.dumps(
                {
                    "name": "重复预订处理Rubric",
                    "description": "验证重复订单并安全取消",
                    "criteria": [
                        {
                            "name": "先验证重复",
                            "description": "取消前必须确认哪一单是重复订单",
                            "required": True,
                            "weight": 0.6,
                        },
                        {
                            "name": "只取消重复项",
                            "description": "不得影响有效订单",
                            "required": True,
                            "weight": 0.4,
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            "evidence": "助手读取两个候选预订，确认第二单重复后仅取消该重复项。",
        },
    )

    class FakeOrchestrator:
        async def run(self):
            return (
                ResolvedOperations(
                    upsert_operations=[case_operation],
                    delete_file_contents=[],
                    errors=[],
                ),
                [],
            )

    def fake_get_or_create_react(self, **kwargs):
        extracted_messages.extend(kwargs["messages"])
        return FakeOrchestrator()

    class FakeStreamingUpdater:
        async def submit(self, request):
            assert request.ctx is not None
            isolation_options = dict(request.isolation_options or {})
            assert isolation_options["allowed_memory_types"] is not None
            assert "cases" in isolation_options["allowed_memory_types"]
            extract_context = ExtractContext(request.messages)
            isolation_handler = MemoryIsolationHandler(
                request.ctx,
                extract_context,
                allowed_memory_types=isolation_options.get("allowed_memory_types"),
                allow_self=isolation_options.get("allow_self", True),
                allowed_peer_ids=isolation_options.get("allowed_peer_ids"),
            )
            result = await MemoryUpdater(
                registry=create_default_registry(),
                vikingdb=None,
            ).apply_operations(
                request.operations,
                request.ctx,
                extract_context=extract_context,
                isolation_handler=isolation_handler,
            )
            return SimpleNamespace(
                operations=request.operations,
                apply_result=result,
                request_count=1,
                metadata={},
            )

    async def fake_train_from_extracted_cases(self, *, cases, messages, ctx, **kwargs):
        del ctx, kwargs
        trained_cases.extend(cases)
        assert list(messages) == extracted_messages
        return {"case_count": len(cases), "submitted": len(cases)}

    monkeypatch.setattr(SessionCompressorV3, "_get_or_create_react", fake_get_or_create_react)
    monkeypatch.setattr(
        SessionCompressorV3,
        "train_from_extracted_cases",
        fake_train_from_extracted_cases,
    )
    monkeypatch.setattr(
        "openviking.session.compressor_v3.get_streaming_memory_updater",
        AsyncMock(return_value=FakeStreamingUpdater()),
    )
    monkeypatch.setattr(
        "openviking.session.session.get_openviking_config",
        lambda: SimpleNamespace(
            memory=SimpleNamespace(
                extraction_enabled=True,
                agent_memory_enabled=False,
                session_skill_extraction_enabled=False,
            )
        ),
    )
    monkeypatch.setattr(
        "openviking.session.session.Session._generate_archive_summary_async",
        AsyncMock(return_value="# Summary\n用户要求处理重复预订，助手验证并取消重复项。"),
    )
    monkeypatch.setattr(
        "openviking.core.directories.DirectoryInitializer._ensure_directory_l0_l1_vectors",
        AsyncMock(return_value=None),
    )

    compressor = client._client.service.session_compressor
    assert isinstance(compressor, SessionCompressorV3)
    compressor.streaming_trainer_config = StreamingPolicyTrainerConfig(max_wait_seconds=3600)

    session_id = "v3_case_dialogue_session"
    await client.add_message(
        session_id=session_id,
        role="user",
        content="请帮我处理酒店重复预订，只取消确认是重复的那一单，保留有效订单。",
    )
    await client.add_message(
        session_id=session_id,
        role="assistant",
        content=(
            "我已读取两个预订候选：A 是原始有效订单，B 与 A 时间和房型相同且状态为重复。"
            "我将只取消 B。"
        ),
    )
    await client.add_message(
        session_id=session_id,
        role="assistant",
        content="已取消重复订单 B，保留订单 A，并向用户确认没有影响有效订单。",
    )

    commit = await client.commit_session(session_id)
    task = await _wait_for_task(commit["task_id"])

    assert task["status"] == "completed"
    assert task["result"]["memories_extracted"] == {"memory_write": 1}
    assert [message.role for message in extracted_messages] == ["user", "assistant", "assistant"]
    assert "酒店重复预订" in extracted_messages[0].content
    assert len(trained_cases) == 1
    assert trained_cases[0].name == "重复预订处理"
    assert trained_cases[0].input["summary"] == "用户要求处理重复预订并保留有效订单"
    assert trained_cases[0].rubric.criteria[0].name == "先验证重复"

    memory_file = await client.read(case_operation.uris[0])
    assert "# 重复预订处理" in memory_file
    assert "## Rubric" in memory_file
    assert "只取消确认重复的订单" in memory_file
