# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock

import pytest

from openviking.storage.viking_fs import VikingFS


class _DummyAgfs:
    pass


@pytest.mark.asyncio
async def test_overview_file_falls_back_to_parent_directory(monkeypatch):
    fs = VikingFS(agfs=_DummyAgfs())

    file_uri = "viking://resources/demo/file.md"
    file_path = "/local/default/resources/demo/file.md"
    parent_uri = "viking://resources/demo"
    parent_path = "/local/default/resources/demo"
    overview_path = f"{parent_path}/.overview.md"
    expected = "# Demo\n\nParent overview"

    stat_calls: list[str] = []
    read_calls: list[str] = []

    async def fake_stat(path):
        stat_calls.append(path)
        if path == file_path:
            return {"isDir": False}
        if path == parent_path:
            return {"isDir": True}
        raise AssertionError(f"unexpected stat path: {path}")

    async def fake_read(path):
        read_calls.append(path)
        if path == overview_path:
            return expected.encode()
        raise AssertionError(f"unexpected read path: {path}")

    monkeypatch.setattr(fs, "_ensure_access", lambda uri, ctx=None: None)
    monkeypatch.setattr(
        fs, "_uri_to_path", lambda uri, ctx=None: uri.replace("viking://", "/local/default/")
    )
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda path, ctx=None: path.replace("/local/default/", "viking://")
    )
    monkeypatch.setattr(fs, "_handle_agfs_read", lambda data: data)
    monkeypatch.setattr(fs, "_decode_bytes", lambda data: data.decode())
    monkeypatch.setattr(fs._async_agfs, "stat", AsyncMock(side_effect=fake_stat))
    monkeypatch.setattr(fs._async_agfs, "read", AsyncMock(side_effect=fake_read))

    overview = await fs.overview(file_uri)

    assert overview == expected
    assert stat_calls == [file_path, parent_path]
    assert read_calls == [overview_path]
    assert overview_path not in stat_calls
    assert parent_uri == "viking://resources/demo"
