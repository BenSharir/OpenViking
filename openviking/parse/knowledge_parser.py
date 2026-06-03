# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
KnowledgeParser: Integrate with a third-party knowledge_base_server for parsing.

Workflow:
1. Submit a parse task (submit)
2. Poll task status (get_task_info) until success/failed
3. Download result (zip_url or chunks)
4. Materialize the result into VikingFS temp directory
5. Return ParseResult for downstream TreeBuilder/SemanticQueue processing
"""
import os
import json
import asyncio
import mimetypes
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional, Union

import httpx
from volcengine.auth.SignerV4 import SignerV4
from volcengine.base.Request import Request
from volcengine.Credentials import Credentials

from openviking.parse.base import ParseResult, ResourceNode, NodeType
from openviking.parse.parsers.base_parser import BaseParser
from openviking.storage.viking_fs import get_viking_fs
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class KnowledgeParser(BaseParser):
    """
    KnowledgeParser: Third-party parse client.
    """

    def __init__(self):
        self._api_host = os.environ.get(
            "KNOWLEDGE_PARSER_HOST",
            "http://localhost:8089",
        ).rstrip("/")
        self._upload_host = os.environ.get("KNOWLEDGE_PARSER_UPLOAD_HOST", self._api_host).rstrip("/")
        self._http_timeout_sec = float(os.environ.get("KNOWLEDGE_PARSER_HTTP_TIMEOUT_SEC", "10"))
        self._timeout_sec = int(os.environ.get("KNOWLEDGE_PARSER_TIMEOUT_SEC", "1800"))
        self._default_poll_interval_ms = int(
            os.environ.get("KNOWLEDGE_PARSER_POLL_INTERVAL_MS", "3000")
        )
        self._account_id = os.environ.get("KNOWLEDGE_PARSER_ACCOUNT_ID")
        self._kb_env = os.environ.get("KNOWLEDGE_PARSER_KB_ENV")
        self._ak = os.environ.get("KNOWLEDGE_PARSER_AK")
        self._sk = os.environ.get("KNOWLEDGE_PARSER_SK")
        self._service = os.environ.get("KNOWLEDGE_PARSER_SERVICE", "air")
        self._region = os.environ.get("KNOWLEDGE_PARSER_REGION", "cn-north-1")
        self._session_token = os.environ.get("KNOWLEDGE_PARSER_SESSION_TOKEN") or ""
        self._upload_simple_max_bytes = int(
            os.environ.get("KNOWLEDGE_PARSER_UPLOAD_SIMPLE_MAX_BYTES", str(100 * 1024 * 1024))
        )
        self._upload_part_size_bytes = int(
            os.environ.get("KNOWLEDGE_PARSER_UPLOAD_PART_SIZE_BYTES", str(8 * 1024 * 1024))
        )

        self._video_exts = {"mp4", "mov", "avi", "flv", "mkv", "wmv", "webm"}
        self._audio_exts = {"mp3", "wav", "m4a", "flac", "aac", "ogg"}

    @property
    def supported_extensions(self) -> List[str]:
        return [".pdf", ".docx", ".pptx", ".xlsx", ".mp4", ".mp3", ".wav", ".mov"]

    async def parse(self, source: Union[str, Path], instruction: str = "", **kwargs) -> ParseResult:
        """
        Parse via third-party API.

        - For remote resources: accept http(s) URL.
        - For local video/audio files: upload to base_server to obtain a presigned URL, then submit.
        """
        source_str = str(source)
        original_source = kwargs.get("original_source")
        candidate = original_source if isinstance(original_source, str) else source_str

        url: Optional[str] = None
        local_path: Optional[Path] = None
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            url = candidate
            parsed = urlparse(url)
            doc_name = Path(parsed.path).stem or "resource"
            doc_type = Path(parsed.path).suffix.lower().lstrip(".") or "unknown"
        else:
            local_path = Path(candidate)
            if not local_path.is_file():
                raise ValueError(
                    "KnowledgeParser supports http(s) URLs or local video/audio files. "
                    f"Got source={source_str!r} original_source={original_source!r}."
                )
            doc_name = local_path.stem or "resource"
            doc_type = local_path.suffix.lower().lstrip(".") or "unknown"

        account_id = kwargs.get("account_id") or self._account_id
        if not account_id:
            raise ValueError(
                "KnowledgeParser requires request header V-Account-Id. "
                "Set env KNOWLEDGE_PARSER_ACCOUNT_ID or pass account_id in add_resource()."
            )

        if url is None and local_path is not None:
            if doc_type not in (self._video_exts | self._audio_exts):
                raise ValueError(
                    "KnowledgeParser only supports local video/audio files. "
                    f"Got file={str(local_path)!r} doc_type={doc_type!r}."
                )
            url, upload_meta = await self._upload_local_file(
                file_path=local_path,
                account_id=str(account_id),
            )
        else:
            upload_meta = {}

        logger.info(f"[KnowledgeParser] submit: url={url} doc_type={doc_type}")
        task_info = await self._submit_task(
            url=url,
            doc_type=doc_type,
            doc_name=doc_name,
            account_id=str(account_id),
        )
        task_id = task_info["task_id"]
        poll_ms = int(task_info.get("next_poll_after_ms") or self._default_poll_interval_ms)
        data = await self._wait_done(task_id=task_id, poll_interval_ms=poll_ms, account_id=str(account_id))
        result_obj = (data.get("result") or {}) if isinstance(data, dict) else {}
        zip_url = result_obj.get("zip_url")
        task_meta = {
            "task_id": task_id,
            "api_host": self._api_host,
            "zip_object_key": result_obj.get("zip_object_key"),
            "cost_ms": result_obj.get("cost_ms"),
        }
        if upload_meta:
            task_meta["upload"] = upload_meta

        if zip_url:
            zip_path = await self._download_zip(zip_url)
            try:
                temp_dir_path = await self._unpack_zip_to_temp_dir(
                    zip_path=zip_path,
                    resource_name=doc_name,
                )
            finally:
                try:
                    zip_path.unlink()
                except Exception:
                    pass
        else:
            chunks = result_obj.get("chunks") or []
            temp_dir_path = await self._write_chunks_to_temp_dir(
                chunks=chunks,
                resource_name=doc_name,
            )

        content_type = "video" if doc_type in self._video_exts else "audio" if doc_type in self._audio_exts else "text"
        root_node = ResourceNode(
            type=NodeType.ROOT,
            title=doc_name,
            level=0,
            detail_file=None,
            content_path=None,
            meta={
                "source_title": doc_name,
                "semantic_name": doc_name,
                "original_filename": f"{doc_name}.{doc_type}" if doc_type else doc_name,
            },
            content_type=content_type,
        )

        result = ParseResult(
            root=root_node,
            source_path=url or source_str,
            source_format=doc_type,
            temp_dir_path=temp_dir_path,
            parser_name="KnowledgeParser",
            meta=task_meta,
        )

        logger.info(f"[KnowledgeParser] done: source={result.source_path} -> {result.temp_dir_path}")
        return result

    async def parse_content(
        self, content: str, source_path: Optional[str] = None, instruction: str = "", **kwargs
    ) -> ParseResult:
        """
        Not supported. Use parse() with an http(s) URL.
        """
        raise ValueError("KnowledgeParser.parse_content is not supported. Use parse() with an http(s) URL.")

    def _json_bytes(self, obj: Any) -> bytes:
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")

    def _maybe_sign_headers(
        self,
        *,
        method: str,
        url: str,
        headers: Dict[str, str],
        params: Optional[Dict[str, Any]],
        body: Optional[Union[str, bytes]],
    ) -> Dict[str, str]:
        if not (self._ak and self._sk):
            return headers

        parsed = urlparse(url)
        host = parsed.netloc
        scheme = parsed.scheme or "https"
        path = parsed.path or "/"

        r = Request()
        r.set_shema(scheme)
        r.set_method(method.upper())
        r.set_connection_timeout(max(1, int(self._http_timeout_sec)))
        r.set_socket_timeout(max(1, int(self._http_timeout_sec)))

        signed_headers = dict(headers)
        signed_headers.setdefault("Host", host)
        signed_headers.setdefault("Accept", "application/json")
        r.set_headers(signed_headers)
        if params:
            normalized_params: Dict[str, Any] = {}
            for k, v in params.items():
                if isinstance(v, (int, float, bool)):
                    normalized_params[k] = str(v)
                elif isinstance(v, list):
                    normalized_params[k] = ",".join([str(x) for x in v])
                else:
                    normalized_params[k] = v
            r.set_query(normalized_params)
        r.set_host(host)
        r.set_path(path)
        if body is not None:
            r.set_body(body)

        credentials = Credentials(
            self._ak,
            self._sk,
            self._service,
            self._region,
            session_token=self._session_token,
        )
        SignerV4.sign(r, credentials)
        return dict(r.headers)

    async def _submit_task(
        self, url: str, doc_type: str, doc_name: str, account_id: str
    ) -> Dict[str, Any]:
        submit_url = f"{self._api_host}/api/knowledge/task/parse_doc/submit"
        headers = {"Content-Type": "application/json;charset=UTF-8"}
        payload: Dict[str, Any] = {
            "url": url,
            "doc_type": doc_type,
            "doc_name": doc_name,
        }
        payload["account_id"] = account_id
        headers["V-Account-Id"] = str(account_id)
        if self._kb_env:
            headers["x-kb-env"] = self._kb_env

        body = self._json_bytes(payload)
        headers = self._maybe_sign_headers(
            method="POST",
            url=submit_url,
            headers=headers,
            params=None,
            body=body,
        )
        async with httpx.AsyncClient(timeout=self._http_timeout_sec, follow_redirects=True) as client:
            rsp = await client.post(submit_url, content=body, headers=headers)
        rsp.raise_for_status()
        body = rsp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"submit failed: code={body.get('code')} message={body.get('message')}")
        data = body.get("data") or {}
        if not data.get("task_id"):
            raise RuntimeError(f"submit missing task_id: {body}")
        return data

    async def _wait_done(self, task_id: str, poll_interval_ms: int, account_id: str) -> Dict[str, Any]:
        info_url = f"{self._api_host}/api/knowledge/task/parse_doc/get_task_info"
        headers = {"Content-Type": "application/json;charset=UTF-8"}
        deadline = asyncio.get_running_loop().time() + float(self._timeout_sec)
        last_status = None
        headers["V-Account-Id"] = str(account_id)
        if self._kb_env:
            headers["x-kb-env"] = self._kb_env

        async with httpx.AsyncClient(timeout=self._http_timeout_sec, follow_redirects=True) as client:
            while True:
                if asyncio.get_running_loop().time() > deadline:
                    raise TimeoutError(f"knowledge parser timeout: task_id={task_id} last_status={last_status}")

                req_body = self._json_bytes({"task_id": task_id})
                req_headers = self._maybe_sign_headers(
                    method="POST",
                    url=info_url,
                    headers=dict(headers),
                    params=None,
                    body=req_body,
                )
                rsp = await client.post(info_url, content=req_body, headers=req_headers)
                rsp.raise_for_status()
                body = rsp.json()
                if body.get("code") != 0:
                    raise RuntimeError(
                        f"get_task_info failed: code={body.get('code')} message={body.get('message')}"
                    )
                data = body.get("data") or {}
                status = data.get("status")
                if status != last_status:
                    logger.info(f"[KnowledgeParser] task_id={task_id} status={status}")
                    last_status = status
                if status in {"success", "failed"}:
                    if status == "failed":
                        err = data.get("error") or {}
                        raise RuntimeError(
                            f"knowledge parser failed: task_id={task_id} "
                            f"error_code={err.get('error_code')} error_msg={err.get('error_msg')}"
                        )
                    return data
                await asyncio.sleep(max(poll_interval_ms, 200) / 1000.0)

    async def _download_zip(self, zip_url: str) -> Path:
        logger.info(f"[KnowledgeParser] download zip: {zip_url}")
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            rsp = await client.get(zip_url)
        rsp.raise_for_status()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as f:
            f.write(rsp.content)
            return Path(f.name)

    async def _unpack_zip_to_temp_dir(self, zip_path: Path, resource_name: str) -> str:
        viking_fs = get_viking_fs()
        temp_uri = viking_fs.create_temp_uri()
        await viking_fs.mkdir(temp_uri)

        temp_doc_uri = f"{temp_uri}/{resource_name}"
        await viking_fs.mkdir(temp_doc_uri)

        with tempfile.TemporaryDirectory() as extract_dir:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
            extract_path = Path(extract_dir)
            items = [p for p in extract_path.iterdir() if p.name not in {".", ".."}]
            if len(items) == 1 and items[0].is_dir():
                root_dir = items[0]
            else:
                root_dir = extract_path

            for child in root_dir.iterdir():
                if child.name in {".", ".."}:
                    continue
                if child.is_dir():
                    sub_uri = f"{temp_doc_uri}/{child.name}"
                    await viking_fs.mkdir(sub_uri)
                    await self._copy_dir_to_fs(child, sub_uri)
                else:
                    await viking_fs.write_file_bytes(f"{temp_doc_uri}/{child.name}", child.read_bytes())

        return temp_uri

    async def _write_chunks_to_temp_dir(
        self, chunks: Union[List[Dict[str, Any]], List[str], Any], resource_name: str
    ) -> str:
        viking_fs = get_viking_fs()
        temp_uri = viking_fs.create_temp_uri()
        await viking_fs.mkdir(temp_uri)

        temp_doc_uri = f"{temp_uri}/{resource_name}"
        await viking_fs.mkdir(temp_doc_uri)

        texts = []
        if isinstance(chunks, list):
            for c in chunks:
                if isinstance(c, dict):
                    t = c.get("text") or ""
                else:
                    t = str(c)
                if t:
                    texts.append(t)

        content = "\n\n---\n\n".join(texts) if texts else ""
        await viking_fs.write_file_bytes(f"{temp_doc_uri}/content.md", content.encode("utf-8"))

        abstract = content[:200] if content else ""
        overview = content[:2000] if content else ""
        await viking_fs.write_file_bytes(f"{temp_doc_uri}/.abstract.md", abstract.encode("utf-8"))
        await viking_fs.write_file_bytes(f"{temp_doc_uri}/.overview.md", overview.encode("utf-8"))

        return temp_uri

    async def _copy_dir_to_fs(self, local_dir: Path, fs_uri: str):
        """
        Recursively copy a local directory to VikingFS.
        """
        viking_fs = get_viking_fs()

        for item in local_dir.iterdir():
            if item.name in [".", ".."]:
                continue

            if item.is_dir():
                sub_uri = f"{fs_uri}/{item.name}"
                await viking_fs.mkdir(sub_uri)
                await self._copy_dir_to_fs(item, sub_uri)
            else:
                file_content = item.read_bytes()
                file_uri = f"{fs_uri}/{item.name}"
                await viking_fs.write_file_bytes(file_uri, file_content)

    async def _post_base_server(
        self,
        path: str,
        account_id: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        content: Any = None,
        headers: Optional[Dict[str, str]] = None,
        timeout_sec: float = 600.0,
    ) -> Dict[str, Any]:
        url = f"{self._upload_host}{path}"
        req_headers = dict(headers or {})
        req_headers.setdefault("V-Account-Id", str(account_id))
        if self._kb_env:
            req_headers.setdefault("x-kb-env", self._kb_env)

        body: Optional[Union[str, bytes]] = None
        if json is not None:
            req_headers.setdefault("Content-Type", "application/json;charset=UTF-8")
            body = self._json_bytes(json)
        elif content is not None:
            body = content

        req_headers = self._maybe_sign_headers(
            method="POST",
            url=url,
            headers=req_headers,
            params=params,
            body=body,
        )
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            rsp = await client.post(url, params=params, content=body, headers=req_headers)
        rsp.raise_for_status()
        body = rsp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"base_server error: {body}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"base_server invalid response: {body}")
        return data

    async def _upload_local_file(self, file_path: Path, account_id: str) -> tuple[str, Dict[str, Any]]:
        file_size = file_path.stat().st_size
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        if file_size <= self._upload_simple_max_bytes:
            data = await self._simple_upload(
                file_path=file_path,
                account_id=account_id,
                content_type=content_type,
            )
        else:
            data = await self._multipart_upload(
                file_path=file_path,
                account_id=account_id,
                content_type=content_type,
            )
        presigned_url = data.get("presigned_url")
        if not presigned_url:
            raise RuntimeError(f"upload missing presigned_url: {data}")
        meta = {
            "object_key": data.get("object_key"),
            "presigned_url": presigned_url,
            "size": data.get("size", file_size),
            "expires_at": data.get("expires_at"),
            "content_type": content_type,
            "upload_method": "simple" if file_size <= self._upload_simple_max_bytes else "multipart",
            "upload_host": self._upload_host,
        }
        return presigned_url, meta

    async def _simple_upload(self, file_path: Path, account_id: str, content_type: str) -> Dict[str, Any]:
        headers = {"Content-Type": "application/octet-stream"}
        with open(file_path, "rb") as f:
            content = f.read()
            return await self._post_base_server(
                "/api/knowledge/upload/simple",
                account_id,
                params={"file_name": file_path.name, "content_type": content_type},
                content=content,
                headers=headers,
                timeout_sec=1200.0,
            )

    async def _multipart_upload(self, file_path: Path, account_id: str, content_type: str) -> Dict[str, Any]:
        min_part_size = 5 * 1024 * 1024
        part_size = max(self._upload_part_size_bytes, min_part_size)
        init_data = await self._post_base_server(
            "/api/knowledge/upload/multipart/init",
            account_id,
            json={
                "file_name": file_path.name,
                "file_size": file_path.stat().st_size,
                "content_type": content_type,
                "part_size": part_size,
            },
            timeout_sec=600.0,
        )
        upload_id = init_data.get("upload_id")
        object_key = init_data.get("object_key")
        server_part_size = int(init_data.get("part_size") or part_size)
        if not upload_id or not object_key:
            raise RuntimeError(f"multipart init missing fields: {init_data}")

        parts: Dict[int, str] = {}
        file_size = file_path.stat().st_size
        total_parts = (file_size + server_part_size - 1) // server_part_size
        headers = {"Content-Type": "application/octet-stream"}

        with open(file_path, "rb") as f:
            for n in range(1, total_parts + 1):
                offset = (n - 1) * server_part_size
                length = min(server_part_size, file_size - offset)
                f.seek(offset)
                chunk = f.read(length)
                part_data = await self._post_base_server(
                    "/api/knowledge/upload/multipart/part",
                    account_id,
                    params={
                        "upload_id": upload_id,
                        "object_key": object_key,
                        "part_number": n,
                    },
                    content=chunk,
                    headers=headers,
                    timeout_sec=1200.0,
                )
                etag = part_data.get("etag")
                if not etag:
                    raise RuntimeError(f"multipart part missing etag: part={n} resp={part_data}")
                parts[n] = etag

        complete_data = await self._post_base_server(
            "/api/knowledge/upload/multipart/complete",
            account_id,
            json={
                "upload_id": upload_id,
                "object_key": object_key,
                "parts": [{"part_number": n, "etag": e} for n, e in sorted(parts.items())],
            },
            timeout_sec=600.0,
        )
        return complete_data
