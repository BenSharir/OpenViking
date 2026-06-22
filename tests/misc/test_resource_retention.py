import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from openviking.server.identity import RequestContext
from openviking.utils.resource_processor import ResourceProcessor
from openviking_cli.utils.config.storage_config import ResourceRetentionConfig


class _DummyVikingDB:
    def get_embedder(self):
        return None


@pytest.fixture
def request_context():
    ctx = MagicMock(spec=RequestContext)
    ctx.account_id = "test_account"
    ctx.user = MagicMock()
    ctx.user.user_id = "test_user"
    return ctx


@pytest.fixture
def resource_processor():
    return ResourceProcessor(vikingdb=_DummyVikingDB(), media_storage=None)


def _make_config(*, max_versions: int, prune_on_import: bool) -> SimpleNamespace:
    return SimpleNamespace(
        storage=SimpleNamespace(
            retention=ResourceRetentionConfig(
                max_versions=max_versions,
                max_age_days=0,
                prune_on_import=prune_on_import,
            )
        )
    )


@pytest.mark.asyncio
async def test_find_numbered_versions_finds_all_versions(monkeypatch, resource_processor, request_context):
    fake_fs = AsyncMock()
    fake_fs.ls.return_value = [
        {"name": "foo", "uri": "viking://resources/foo", "mtime": 100.0},
        {"name": "foo_2", "uri": "viking://resources/foo_2", "mtime": 300.0},
        {"name": "other_dir", "uri": "viking://resources/other_dir", "mtime": 999.0},
        {"name": "foo_1", "uri": "viking://resources/foo_1", "mtime": 200.0},
        {"name": "foo_extra", "uri": "viking://resources/foo_extra", "mtime": 400.0},
    ]
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)

    versions = await resource_processor._find_numbered_versions(
        "viking://resources/foo",
        request_context,
    )

    assert versions == [
        ("viking://resources/foo", 0, 100.0),
        ("viking://resources/foo_1", 1, 200.0),
        ("viking://resources/foo_2", 2, 300.0),
    ]
    fake_fs.ls.assert_awaited_once_with("viking://resources", ctx=request_context)


@pytest.mark.asyncio
async def test_find_numbered_versions_empty_when_no_matches(monkeypatch, resource_processor, request_context):
    fake_fs = AsyncMock()
    fake_fs.ls.return_value = [
        {"name": "bar", "uri": "viking://resources/bar", "mtime": 100.0},
        {"name": "foo_suffix", "uri": "viking://resources/foo_suffix", "mtime": 200.0},
    ]
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)

    versions = await resource_processor._find_numbered_versions(
        "viking://resources/foo",
        request_context,
    )

    assert versions == []


@pytest.mark.asyncio
async def test_find_numbered_versions_handles_ls_error(monkeypatch, resource_processor, request_context):
    fake_fs = AsyncMock()
    fake_fs.ls.side_effect = RuntimeError("boom")
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)

    versions = await resource_processor._find_numbered_versions(
        "viking://resources/foo",
        request_context,
    )

    assert versions == []


@pytest.mark.asyncio
async def test_prune_old_versions_deletes_oldest(monkeypatch, resource_processor, request_context):
    fake_fs = AsyncMock()
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    versions = [
        ("viking://resources/foo", 0, 100.0),
        ("viking://resources/foo_1", 1, 200.0),
        ("viking://resources/foo_2", 2, 300.0),
        ("viking://resources/foo_3", 3, 400.0),
    ]

    deleted = await resource_processor._prune_old_versions(versions, max_keep=2, ctx=request_context)

    assert deleted == 2
    fake_fs.rm.assert_has_awaits(
        [
            call("viking://resources/foo", recursive=True, ctx=request_context),
            call("viking://resources/foo_1", recursive=True, ctx=request_context),
        ]
    )


@pytest.mark.asyncio
async def test_prune_old_versions_skips_when_under_limit(monkeypatch, resource_processor, request_context):
    fake_fs = AsyncMock()
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    versions = [
        ("viking://resources/foo", 0, 100.0),
        ("viking://resources/foo_1", 1, 200.0),
    ]

    deleted = await resource_processor._prune_old_versions(versions, max_keep=2, ctx=request_context)

    assert deleted == 0
    fake_fs.rm.assert_not_awaited()


@pytest.mark.asyncio
async def test_prune_old_versions_handles_delete_error(
    monkeypatch,
    resource_processor,
    request_context,
    caplog,
):
    fake_fs = AsyncMock()

    async def rm_side_effect(uri, recursive=True, ctx=None):
        if uri == "viking://resources/foo":
            raise RuntimeError("cannot delete")
        return None

    fake_fs.rm.side_effect = rm_side_effect
    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    versions = [
        ("viking://resources/foo", 0, 100.0),
        ("viking://resources/foo_1", 1, 200.0),
        ("viking://resources/foo_2", 2, 300.0),
    ]

    with caplog.at_level("WARNING"):
        deleted = await resource_processor._prune_old_versions(versions, max_keep=1, ctx=request_context)

    assert deleted == 1
    assert "Failed to prune viking://resources/foo" in caplog.text
    assert fake_fs.rm.await_count == 2


@pytest.mark.asyncio
async def test_reserve_unique_candidate_prunes_when_enabled(
    monkeypatch,
    resource_processor,
    request_context,
): 
    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=False)
    fake_fs._uri_to_path = MagicMock(return_value="/mock/resources/foo")
    fake_lock = object()
    versions = [("viking://resources/foo", 0, 100.0)]

    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: MagicMock(name="lock_manager"),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _make_config(max_versions=2, prune_on_import=True),
    )
    resource_processor.acquire_resource_lock = AsyncMock(return_value=fake_lock)
    resource_processor._find_numbered_versions = AsyncMock(return_value=versions)
    resource_processor._prune_old_versions = AsyncMock()

    root_uri, lock = await resource_processor.reserve_unique_candidate(
        candidate_uri="viking://resources/foo",
        ctx=request_context,
    )

    assert root_uri == "viking://resources/foo"
    assert lock is fake_lock
    resource_processor._find_numbered_versions.assert_awaited_once_with(
        "viking://resources/foo",
        request_context,
    )
    resource_processor._prune_old_versions.assert_awaited_once_with(
        versions,
        max_keep=2,
        ctx=request_context,
    )


@pytest.mark.asyncio
async def test_reserve_unique_candidate_skips_prune_when_disabled(
    monkeypatch,
    resource_processor,
    request_context,
): 
    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=False)
    fake_fs._uri_to_path = MagicMock(return_value="/mock/resources/foo")

    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: MagicMock(name="lock_manager"),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _make_config(max_versions=0, prune_on_import=True),
    )
    resource_processor.acquire_resource_lock = AsyncMock(return_value=object())
    resource_processor._find_numbered_versions = AsyncMock()
    resource_processor._prune_old_versions = AsyncMock()

    await resource_processor.reserve_unique_candidate(
        candidate_uri="viking://resources/foo",
        ctx=request_context,
    )

    resource_processor._find_numbered_versions.assert_not_awaited()
    resource_processor._prune_old_versions.assert_not_awaited()


@pytest.mark.asyncio
async def test_reserve_unique_candidate_skips_prune_when_prune_on_import_false(
    monkeypatch,
    resource_processor,
    request_context,
): 
    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=False)
    fake_fs._uri_to_path = MagicMock(return_value="/mock/resources/foo")

    monkeypatch.setattr("openviking.utils.resource_processor.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: MagicMock(name="lock_manager"),
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _make_config(max_versions=2, prune_on_import=False),
    )
    resource_processor.acquire_resource_lock = AsyncMock(return_value=object())
    resource_processor._find_numbered_versions = AsyncMock()
    resource_processor._prune_old_versions = AsyncMock()

    await resource_processor.reserve_unique_candidate(
        candidate_uri="viking://resources/foo",
        ctx=request_context,
    )

    resource_processor._find_numbered_versions.assert_not_awaited()
    resource_processor._prune_old_versions.assert_not_awaited()
