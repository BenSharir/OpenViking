# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openviking.server.identity import AccountNamespacePolicy, RequestContext, Role
from openviking.session.memory.agent_experience_context_provider import (
    AgentExperienceContextProvider,
)
from openviking_cli.session.user_id import UserIdentifier


def test_create_tool_context_uses_extract_context_page_id_map():
    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="album release party discussion",
        trajectory_uri="viking://agent/agent_sample_9/memories/trajectories/album_release_party_discussion.md",
    )

    extract_context = provider.get_extract_context()
    extract_context.page_id_map.get_page_id(
        "viking://agent/agent_sample_9/memories/trajectories/album_release_party_discussion.md"
    )

    tool_ctx = provider.create_tool_context()

    assert tool_ctx.page_id_map is extract_context.page_id_map


@pytest.mark.asyncio
async def test_agent_experience_prefetch_starts_with_conversation_and_new_trajectory_read():
    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="album release party discussion",
        trajectory_uri="viking://agent/agent_sample_9/memories/trajectories/album_release_party_discussion.md",
    )
    provider._ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1", agent_id="agent_sample_9"),
        role=Role.USER,
        namespace_policy=AccountNamespacePolicy(),
    )
    provider._viking_fs = AsyncMock()
    provider._transaction_handle = None
    provider.search_files = AsyncMock(return_value=[])

    with patch(
        "openviking.session.memory.agent_experience_context_provider.add_tool_call_pair_to_messages"
    ) as add_tool_call_pair:
        messages = await provider.prefetch()

    assert messages[0]["role"] == "user"
    assert "## Conversation History" in messages[0]["content"]
    assert "After exploring, analyze the conversation" in messages[0]["content"]
    assert add_tool_call_pair.call_count == 1
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["context_role"] == "new_trajectory"
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["memory_type"] == "trajectories"
    assert add_tool_call_pair.call_args_list[0].kwargs["result"]["uri"] == provider.trajectory_uri
    assert messages[-1]["role"] == "user"
    assert "candidate_experience" in messages[-1]["content"]


@pytest.mark.asyncio
async def test_agent_experience_prefetch_includes_structured_read_results():
    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="album release party discussion",
        trajectory_uri="viking://agent/agent_sample_9/memories/trajectories/album_release_party_discussion.md",
    )
    provider._ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1", agent_id="agent_sample_9"),
        role=Role.USER,
        namespace_policy=AccountNamespacePolicy(),
    )
    provider._viking_fs = AsyncMock()
    provider._transaction_handle = None

    provider.search_files = AsyncMock(
        return_value=[
            "viking://agent/agent_sample_9/memories/experiences/personal_experience_sharing_conversation_flow.md"
        ]
    )

    read_result = {
        "experience_name": "personal_experience_sharing_conversation_flow",
        "content": "1 | line one\n2 | line two",
        "page_id": 1,
        "memory_type": "experiences",
    }
    provider.read_file = AsyncMock(return_value=read_result)
    provider._read_file_contents = {
        "viking://agent/agent_sample_9/memories/experiences/personal_experience_sharing_conversation_flow.md": SimpleNamespace(
            extra_fields={"experience_name": "personal_experience_sharing_conversation_flow"},
            content="line one\nline two",
            links=[],
        )
    }

    with patch(
        "openviking.session.memory.agent_experience_context_provider.add_tool_call_pair_to_messages"
    ) as add_tool_call_pair:
        messages = await provider.prefetch()

    assert any(msg.get("role") == "user" for msg in messages)
    assert add_tool_call_pair.call_count == 2
    assert (
        add_tool_call_pair.call_args_list[1].kwargs["result"]["context_role"]
        == "candidate_experience"
    )
    assert add_tool_call_pair.call_args_list[1].kwargs["result"]["page_id"] == 1


@pytest.mark.asyncio
async def test_load_source_trajectories_ignores_non_trajectory_derived_from_links():
    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="album release party discussion",
        trajectory_uri="viking://agent/agent_sample_9/memories/trajectories/album_release_party_discussion.md",
    )
    provider._ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1", agent_id="agent_sample_9"),
        role=Role.USER,
        namespace_policy=AccountNamespacePolicy(),
    )

    viking_fs = AsyncMock()
    viking_fs.read_file.return_value = "trajectory body"

    with patch(
        "openviking.session.memory.agent_experience_context_provider.MemoryFileUtils.read"
    ) as read_memory:
        read_memory.return_value = SimpleNamespace(
            to_metadata=lambda: {"memory_type": "trajectories"},
            content="trajectory body",
        )
        result = await provider._load_source_trajectories(
            "viking://agent/agent_sample_9/memories/experiences/personal_experience_sharing_conversation_flow.md",
            links=[
                {
                    "link_type": "derived_from",
                    "to_uri": "viking://agent/conv-42/memories/experiences/extract_conversation_interests.md",
                },
                {
                    "link_type": "derived_from",
                    "to_uri": "viking://agent/agent_sample_9/memories/trajectories/real-traj.md",
                },
            ],
            viking_fs=viking_fs,
            ctx=provider._ctx,
        )

    assert [item["uri"] for item in result] == [
        "viking://agent/agent_sample_9/memories/trajectories/real-traj.md"
    ]
    viking_fs.read_file.assert_awaited_once_with(
        "viking://agent/agent_sample_9/memories/trajectories/real-traj.md",
        ctx=provider._ctx,
    )


@pytest.mark.asyncio
async def test_load_source_trajectories_logs_warning_when_trajectory_missing():
    provider = AgentExperienceContextProvider(
        messages=[],
        trajectory_summary="album release party discussion",
        trajectory_uri="viking://agent/agent_sample_9/memories/trajectories/album_release_party_discussion.md",
    )
    provider._ctx = RequestContext(
        user=UserIdentifier(account_id="acc", user_id="user_1", agent_id="agent_sample_9"),
        role=Role.USER,
        namespace_policy=AccountNamespacePolicy(),
    )

    viking_fs = AsyncMock()
    viking_fs.read_file.side_effect = FileNotFoundError("File not found")

    with patch(
        "openviking.session.memory.agent_experience_context_provider.logger.warning"
    ) as warning:
        result = await provider._load_source_trajectories(
            "viking://agent/agent_sample_9/memories/experiences/personal_experience_sharing_conversation_flow.md",
            links=[
                {
                    "link_type": "derived_from",
                    "to_uri": "viking://agent/agent_sample_9/memories/trajectories/missing-traj.md",
                }
            ],
            viking_fs=viking_fs,
            ctx=provider._ctx,
        )

    assert result == []
    warning.assert_called_once()
