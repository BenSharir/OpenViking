# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Session Compressor V3.

V3 keeps the V2 extraction interface while changing user-memory commits to a
patch-merge flow without directory-level memory locks.  Training cases are not
extracted by a separate LLM call; they are a normal user-memory ``memory_type``
(``cases``) emitted by the same ExtractLoop that extracts profile/preferences/
events/etc.  When such case memories are produced, the same commit rollout is
submitted to the process-global StreamingPolicyTrainer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional
from uuid import uuid4

from openviking.core.context import Context
from openviking.message import Message
from openviking.server.identity import RequestContext
from openviking.session.memory import ExtractLoop, MemoryUpdater, StreamingMemoryUpdaterConfig
from openviking.session.memory.dataclass import (
    MemoryOperationSource,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.memory_isolation_handler import MemoryIsolationHandler
from openviking.session.memory.memory_type_registry import create_default_registry
from openviking.session.memory.memory_updater import ExtractContext
from openviking.session.memory.session_extract_context_provider import (
    SessionExtractContextProvider,
)
from openviking.session.memory.streaming_memory_updater import (
    MemoryUpdateRequest,
    get_streaming_memory_updater,
    make_streaming_memory_updater_key,
)
from openviking.session.memory.utils.json_parser import JsonUtils
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.uri import generate_uri
from openviking.session.train import (
    Case,
    ExperienceGradientContext,
    ExperienceGradientEstimator,
    ExperienceSetLoader,
    MemoryFilePolicyUpdater,
    PatchMergePolicyOptimizer,
    PatchMergePolicyOptimizerContext,
    PipelineContext,
    Rollout,
    Rubric,
    RubricCriterion,
    StreamingPolicyTrainerConfig,
    TrajectoryAnalyzerContext,
    TrajectoryRolloutAnalyzer,
    get_streaming_policy_trainer,
    make_streaming_policy_trainer_key,
)
from openviking.storage.viking_fs import get_viking_fs
from openviking.telemetry import tracer
from openviking_cli.utils import get_logger
from openviking_cli.utils.config import get_openviking_config

logger = get_logger(__name__)

_CASES_MEMORY_TYPE = "cases"
_TRAINING_CASE_SPEC_PROTOCOL = "openviking.batch_train.case_spec.v1"
_TRAINING_CASE_SPEC_HEADER = "# OpenViking Batch Training CaseSpec v1"
_TRAINING_FAST_PATH_MEMORY_TYPES = frozenset({"cases", "trajectories", "experiences"})
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class SessionCompressorV3:
    """Session compressor with lock-free patch-merge user memory extraction."""

    rollout_analyzer: TrajectoryRolloutAnalyzer | Any
    streaming_trainer_config: StreamingPolicyTrainerConfig = field(
        default_factory=StreamingPolicyTrainerConfig
    )
    streaming_memory_updater_config: StreamingMemoryUpdaterConfig = field(
        default_factory=StreamingMemoryUpdaterConfig
    )

    def __init__(
        self,
        vikingdb,
        skill_processor: Optional[Any] = None,
        *,
        rollout_analyzer: TrajectoryRolloutAnalyzer | Any | None = None,
        streaming_trainer_config: StreamingPolicyTrainerConfig | None = None,
        streaming_memory_updater_config: StreamingMemoryUpdaterConfig | None = None,
    ):
        self.vikingdb = vikingdb
        self.skill_processor = skill_processor
        self.rollout_analyzer = rollout_analyzer or TrajectoryRolloutAnalyzer(
            viking_fs=get_viking_fs(),
            vikingdb=vikingdb,
        )
        self.streaming_trainer_config = streaming_trainer_config or StreamingPolicyTrainerConfig()
        self.streaming_memory_updater_config = (
            streaming_memory_updater_config or StreamingMemoryUpdaterConfig()
        )

    def _get_or_create_react(
        self,
        ctx: Optional[RequestContext] = None,
        messages: Optional[List] = None,
        latest_archive_overview: str = "",
        isolation_handler: Optional[MemoryIsolationHandler] = None,
        transaction_handle=None,
    ) -> ExtractLoop:
        config = get_openviking_config()
        vlm = config.vlm.get_vlm_instance()
        viking_fs = get_viking_fs()
        context_provider = SessionExtractContextProvider(
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            isolation_handler=isolation_handler,
            ctx=ctx,
            viking_fs=viking_fs,
            transaction_handle=transaction_handle,
        )
        return ExtractLoop(
            vlm=vlm,
            viking_fs=viking_fs,
            ctx=ctx,
            context_provider=context_provider,
            isolation_handler=isolation_handler,
        )

    def _get_or_create_updater(self, registry, transaction_handle=None) -> MemoryUpdater:
        return MemoryUpdater(
            registry=registry,
            vikingdb=self.vikingdb,
            transaction_handle=transaction_handle,
        )

    async def _build_memory_diff(
        self,
        result: Any,
        operations: ResolvedOperations,
        viking_fs: Any,
        ctx: RequestContext,
        archive_uri: str = "",
    ) -> dict[str, Any]:
        adds: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        deletes: list[dict[str, Any]] = []

        upsert_by_uri = {}
        for op in operations.upsert_operations:
            for uri in op.uris:
                upsert_by_uri[uri] = op
        delete_by_uri = {dc.uri: dc for dc in operations.delete_file_contents}

        for uri in result.written_uris:
            op = upsert_by_uri.get(uri)
            memory_type = op.memory_type if op else _get_memory_type_from_uri(uri)
            old_file = op.old_memory_file_content if op else None
            if old_file:
                updates.append(
                    {
                        "uri": uri,
                        "memory_type": memory_type,
                        "before": old_file.content,
                        "after": "",
                    }
                )
            else:
                adds.append({"uri": uri, "memory_type": memory_type, "after": ""})

        for uri in result.edited_uris:
            op = upsert_by_uri.get(uri)
            memory_type = op.memory_type if op else _get_memory_type_from_uri(uri)
            old_file = op.old_memory_file_content if op and op.old_memory_file_content else None
            updates.append(
                {
                    "uri": uri,
                    "memory_type": memory_type,
                    "before": old_file.content if old_file else "",
                    "after": "",
                }
            )

        for uri in result.deleted_uris:
            deleted = delete_by_uri.get(uri)
            deletes.append(
                {
                    "uri": uri,
                    "memory_type": (deleted.memory_type if deleted else None) or "unknown",
                    "deleted_content": deleted.content if deleted else "",
                }
            )

        for item in adds + updates:
            try:
                content = await viking_fs.read_file(uri=item["uri"], ctx=ctx)
                item["after"] = MemoryFileUtils.read(content).content
            except Exception:
                pass

        return {
            "archive_uri": archive_uri,
            "trace_id": tracer.get_trace_id() or None,
            "extracted_at": datetime.utcnow().isoformat() + "Z",
            "operations": {"adds": adds, "updates": updates, "deletes": deletes},
            "summary": {
                "total_adds": len(adds),
                "total_updates": len(updates),
                "total_deletes": len(deletes),
            },
        }

    @tracer(ignore_result=True)
    async def extract_long_term_memories(
        self,
        messages: List[Message],
        user: Optional[Any] = None,
        session_id: Optional[str] = None,
        ctx: Optional[RequestContext] = None,
        strict_extract_errors: bool = False,
        latest_archive_overview: str = "",
        archive_uri: Optional[str] = None,
        allowed_memory_types: Optional[set[str]] = None,
        allow_self_memory: bool = True,
        allowed_peer_ids: Optional[set[str]] = None,
    ):
        message_list = list(messages)
        fast_path_case = _training_case_from_first_message(message_list, allowed_memory_types)
        if fast_path_case is not None:
            contexts = await self._commit_training_case_fast_path(
                case=fast_path_case,
                messages=message_list,
                ctx=ctx,
                session_id=session_id,
                archive_uri=archive_uri or "",
                strict_extract_errors=strict_extract_errors,
            )
            return contexts

        result = await self._extract_user_memories(
            messages=message_list,
            user=user,
            session_id=session_id,
            ctx=ctx,
            strict_extract_errors=strict_extract_errors,
            latest_archive_overview=latest_archive_overview,
            archive_uri=archive_uri,
            allowed_memory_types=allowed_memory_types,
            allow_self_memory=allow_self_memory,
            allowed_peer_ids=allowed_peer_ids,
        )
        await self.train_from_extracted_cases(
            cases=result.cases,
            messages=message_list,
            ctx=ctx,
            session_id=session_id,
            archive_uri=archive_uri or "",
            strict_extract_errors=strict_extract_errors,
        )
        return result.contexts

    async def _commit_training_case_fast_path(
        self,
        *,
        case: Case,
        messages: list[Message],
        ctx: Optional[RequestContext],
        session_id: Optional[str],
        archive_uri: str,
        strict_extract_errors: bool,
    ) -> list[Context]:
        if ctx is None:
            logger.warning("No RequestContext provided, skipping training case fast path")
            return []
        case_result = await self._write_training_case_memory(
            case=case,
            ctx=ctx,
            archive_uri=archive_uri,
        )
        contexts = _contexts_from_update_result(case_result)
        await self.train_from_extracted_cases(
            cases=[case],
            messages=list(messages[1:]),
            ctx=ctx,
            session_id=session_id,
            archive_uri=archive_uri,
            strict_extract_errors=strict_extract_errors,
        )
        return contexts

    @tracer("train.compressor_v3.fast_path.write_case", ignore_result=True, ignore_args=True)
    async def _write_training_case_memory(
        self,
        *,
        case: Case,
        ctx: RequestContext,
        archive_uri: str,
    ) -> Any:
        viking_fs = get_viking_fs()
        registry = create_default_registry()
        schema = registry.get(_CASES_MEMORY_TYPE)
        if schema is None or not schema.enabled:
            raise RuntimeError("cases memory schema is not available")

        extract_context = ExtractContext([])
        fields = _case_to_memory_fields(case)
        uri = generate_uri(
            memory_type=schema,
            fields=fields,
            user_space=getattr(getattr(ctx, "user", None), "user_id", None) or "default",
            extract_context=extract_context,
        )
        old_file = None
        try:
            raw = await viking_fs.read_file(uri, ctx=ctx)
            if raw:
                old_file = MemoryFileUtils.read(raw, uri=uri)
        except Exception:
            old_file = None

        source = MemoryOperationSource(
            extraction_id=(archive_uri.rstrip("/").rsplit("/", 1)[-1] if archive_uri else ""),
            archive_uri=archive_uri or None,
            trace_id=tracer.get_trace_id() or None,
        )
        operations = ResolvedOperations(
            upsert_operations=[
                ResolvedOperation(
                    old_memory_file_content=old_file,
                    memory_fields=fields,
                    memory_type=_CASES_MEMORY_TYPE,
                    uris=[uri],
                    source=source,
                )
            ],
            delete_file_contents=[],
            errors=[],
        )
        updater = self._get_or_create_updater(registry, transaction_handle=None)
        result = await updater.apply_operations(
            operations,
            ctx,
            extract_context=extract_context,
            isolation_handler=MemoryIsolationHandler(
                ctx,
                extract_context,
                allowed_memory_types={_CASES_MEMORY_TYPE},
            ),
        )
        if archive_uri:
            memory_diff = await self._build_memory_diff(
                result=result,
                operations=operations,
                viking_fs=viking_fs,
                ctx=ctx,
                archive_uri=archive_uri,
            )
            await viking_fs.write_file(
                uri=f"{archive_uri.rstrip('/')}/memory_diff.json",
                content=json.dumps(memory_diff, ensure_ascii=False, indent=4),
                ctx=ctx,
            )
        tracer.info(
            "Training CaseSpec fast path wrote case memory: "
            f"case={case.name} uri={uri} written={result.written_uris} edited={result.edited_uris}"
        )
        return result

    @tracer(
        "train.compressor_v3.extract_user_memories", ignore_result=True, ignore_args=True
    )
    async def _extract_user_memories(
        self,
        messages: List[Message],
        user: Optional[Any] = None,
        session_id: Optional[str] = None,
        ctx: Optional[RequestContext] = None,
        strict_extract_errors: bool = False,
        latest_archive_overview: str = "",
        archive_uri: Optional[str] = None,
        allowed_memory_types: Optional[set[str]] = None,
        allow_self_memory: bool = True,
        allowed_peer_ids: Optional[set[str]] = None,
    ) -> "_V3ExtractionResult":
        del user
        if not messages:
            return _V3ExtractionResult()
        if not ctx:
            logger.warning("No RequestContext provided, skipping v3 memory extraction")
            return _V3ExtractionResult()

        try:
            viking_fs = get_viking_fs()
        except Exception:
            logger.warning("VikingFS unavailable, skipping v3 memory extraction", exc_info=True)
            return _V3ExtractionResult()

        registry = create_default_registry()
        if allow_self_memory:
            await registry.initialize_memory_files(ctx)

        extract_context = ExtractContext(messages)
        isolation_handler = MemoryIsolationHandler(
            ctx,
            extract_context,
            allowed_memory_types=allowed_memory_types,
            allow_self=allow_self_memory,
            allowed_peer_ids=allowed_peer_ids,
        )
        isolation_handler.prepare_messages()

        orchestrator = self._get_or_create_react(
            ctx=ctx,
            messages=messages,
            latest_archive_overview=latest_archive_overview,
            isolation_handler=isolation_handler,
            transaction_handle=None,
        )
        operations, _tools_used = await orchestrator.run()
        if operations is None:
            tracer.info("[v3_patch_merge] No memory operations generated")
            return _V3ExtractionResult()

        extraction_id = uuid4().hex
        extracted_at = datetime.now(timezone.utc).isoformat()
        extracted_cases = _operations_to_cases(operations)

        updater = await get_streaming_memory_updater(
            key=make_streaming_memory_updater_key(request_context=ctx),
            registry=registry,
            vikingdb=self.vikingdb,
            config=self.streaming_memory_updater_config,
        )
        update_result = await updater.submit(
            MemoryUpdateRequest(
                operations=operations,
                messages=list(messages),
                ctx=ctx,
                strict_extract_errors=strict_extract_errors,
                isolation_options={
                    "allowed_memory_types": allowed_memory_types,
                    "allow_self": allow_self_memory,
                    "allowed_peer_ids": allowed_peer_ids,
                },
                metadata={
                    "source_extraction_id": extraction_id,
                    "session_id": session_id,
                    "archive_uri": archive_uri,
                    "trace_id": tracer.get_trace_id(),
                    "extracted_at": extracted_at,
                },
            )
        )

        result = update_result.apply_result
        patch_operations = update_result.operations

        if archive_uri and viking_fs and result is not None:
            memory_diff = await self._build_memory_diff(
                result=result,
                operations=patch_operations,
                viking_fs=viking_fs,
                ctx=ctx,
                archive_uri=archive_uri,
            )
            await viking_fs.write_file(
                uri=f"{archive_uri}/memory_diff.json",
                content=json.dumps(memory_diff, ensure_ascii=False, indent=4),
                ctx=ctx,
            )

        contexts = _contexts_from_update_result(result)
        return _V3ExtractionResult(contexts=contexts, cases=extracted_cases)

    @tracer("train.compressor_v3.train_from_extracted_cases", ignore_result=True, ignore_args=True)
    async def train_from_extracted_cases(
        self,
        *,
        cases: list[Case],
        messages: list[Message],
        ctx: Optional[RequestContext],
        session_id: Optional[str] = None,
        archive_uri: str = "",
        strict_extract_errors: bool = False,
    ) -> dict[str, Any]:
        if not messages or ctx is None:
            return {"case_count": 0, "submitted": 0, "reason": "missing_messages_or_ctx"}
        if not cases:
            tracer.info("No commit training case memories extracted; skipping streaming train")
            return {"case_count": 0, "submitted": 0}

        try:
            viking_fs = get_viking_fs()
            policy_root_uri = _experience_root_uri(ctx)
            policy_set = await ExperienceSetLoader(viking_fs=viking_fs).load(
                policy_root_uri,
                ctx=ctx,
            )
            optimizer_context = PatchMergePolicyOptimizerContext(request_context=ctx)
            gradient_context = ExperienceGradientContext(
                request_context=ctx,
                messages=list(messages),
                strict_extract_errors=strict_extract_errors,
            )
            trainer = await get_streaming_policy_trainer(
                key=make_streaming_policy_trainer_key(
                    policy_root_uri=policy_root_uri,
                    request_context=ctx,
                ),
                policy_set=policy_set,
                rollout_analyzer=self.rollout_analyzer,
                gradient_estimator=ExperienceGradientEstimator(
                    viking_fs=viking_fs,
                ),
                policy_optimizer=PatchMergePolicyOptimizer(
                    viking_fs=viking_fs,
                    memory_type="experiences",
                ),
                policy_updater=MemoryFilePolicyUpdater(viking_fs=viking_fs, vikingdb=self.vikingdb),
                context=PipelineContext(
                    analysis_context=TrajectoryAnalyzerContext(
                        request_context=ctx,
                        strict_extract_errors=strict_extract_errors,
                    ),
                    gradient_context=gradient_context,
                    optimization_context=optimizer_context,
                    apply_context=ctx,
                ),
                config=self.streaming_trainer_config,
            )
            submitted = 0
            for case in cases:
                rollout = Rollout(
                    case=case,
                    messages=list(messages),
                    policy_snapshot_id=_commit_policy_snapshot_id(
                        session_id=session_id,
                        archive_uri=archive_uri,
                    ),
                )
                await trainer.submit_rollout(rollout)
                submitted += 1
            tracer.info(
                "Submitted commit case memories to streaming train: "
                f"case_count={len(cases)} submitted={submitted}",
                console=self.streaming_trainer_config.trace_console,
            )
            return {"case_count": len(cases), "submitted": submitted}
        except Exception as exc:
            logger.warning("Commit streaming train failed: %s", exc, exc_info=True)
            if strict_extract_errors:
                raise
            return {"case_count": len(cases), "submitted": 0, "error": str(exc)}


@dataclass(slots=True)
class _V3ExtractionResult:
    contexts: list[Context] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)


def _contexts_from_update_result(result: Any) -> list[Context]:
    contexts = []
    for uri in result.written_uris:
        contexts.append(Context(uri=uri, category="memory_write", context_type="memory"))
    for uri in result.edited_uris:
        contexts.append(Context(uri=uri, category="memory_edit", context_type="memory"))
    for uri in result.deleted_uris:
        contexts.append(Context(uri=uri, category="memory_delete", context_type="memory"))
    return contexts


def _training_case_from_first_message(
    messages: list[Message],
    allowed_memory_types: Optional[set[str]],
) -> Case | None:
    """Parse a batch-training CaseSpec control message from message[0].

    The fast path is deliberately gated by the commit memory policy so normal
    user sessions never bypass user-memory extraction.  Once the protocol
    header is present, malformed payloads are treated as errors instead of
    silently falling back to LLM extraction.
    """
    if not messages or allowed_memory_types is None:
        return None
    if not set(allowed_memory_types).issubset(_TRAINING_FAST_PATH_MEMORY_TYPES):
        return None

    text = _message_text(messages[0]).strip()
    if not text.startswith(_TRAINING_CASE_SPEC_HEADER):
        return None
    payload = _parse_training_case_spec_payload(text)
    return _case_from_payload(payload)


def _message_text(message: Message) -> str:
    content = getattr(message, "content", "")
    if content:
        return str(content)
    texts: list[str] = []
    for part in getattr(message, "parts", []) or []:
        text = getattr(part, "text", None)
        if text:
            texts.append(str(text))
    return "\n".join(texts)


def _parse_training_case_spec_payload(text: str) -> dict[str, Any]:
    match = _JSON_FENCE_RE.search(text)
    raw_payload = (
        match.group(1).strip()
        if match
        else text.removeprefix(_TRAINING_CASE_SPEC_HEADER).strip()
    )
    if not raw_payload:
        raise ValueError("Training CaseSpec fast path payload is empty")
    try:
        payload = JsonUtils.loads(raw_payload)
    except Exception as exc:
        raise ValueError("Training CaseSpec fast path payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Training CaseSpec fast path payload must be a JSON object")
    protocol = str(payload.get("protocol") or "")
    if protocol != _TRAINING_CASE_SPEC_PROTOCOL:
        raise ValueError(
            "Training CaseSpec fast path protocol mismatch: "
            f"expected {_TRAINING_CASE_SPEC_PROTOCOL!r}, got {protocol!r}"
        )
    if not isinstance(payload.get("case"), dict):
        raise ValueError("Training CaseSpec fast path payload must contain a case object")
    return payload


def _case_from_payload(payload: dict[str, Any]) -> Case:
    raw_case = payload["case"]
    name = str(raw_case.get("name") or "").strip()
    task_signature = str(raw_case.get("task_signature") or "").strip()
    if not name:
        raise ValueError("Training CaseSpec case.name is required")
    if not task_signature:
        raise ValueError("Training CaseSpec case.task_signature is required")
    case_input = raw_case.get("input")
    if not isinstance(case_input, dict):
        raise ValueError("Training CaseSpec case.input must be an object")
    rubric = _rubric_from_payload(raw_case.get("rubric"), fallback_name=f"{name}_rubric")
    metadata = raw_case.get("metadata") if isinstance(raw_case.get("metadata"), dict) else {}
    return Case(
        name=name,
        task_signature=task_signature,
        input=dict(case_input),
        rubric=rubric,
        metadata={
            "source": "batch_training_case_spec",
            **dict(metadata),
        },
    )


def _rubric_from_payload(raw_rubric: Any, *, fallback_name: str) -> Rubric:
    if not isinstance(raw_rubric, dict):
        raise ValueError("Training CaseSpec case.rubric must be an object")
    raw_criteria = raw_rubric.get("criteria")
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise ValueError("Training CaseSpec case.rubric.criteria must be a non-empty list")

    criteria: list[RubricCriterion] = []
    for index, item in enumerate(raw_criteria):
        if not isinstance(item, dict):
            raise ValueError("Training CaseSpec rubric criteria must be objects")
        name = str(item.get("name") or f"criterion_{index + 1}").strip()
        description = str(item.get("description") or "").strip()
        if not description:
            raise ValueError("Training CaseSpec rubric criterion.description is required")
        criteria.append(
            RubricCriterion(
                name=name,
                description=description,
                required=bool(item.get("required", True)),
                weight=_safe_weight(item.get("weight"), default=1.0),
                metadata=dict(item.get("metadata") or {})
                if isinstance(item.get("metadata"), dict)
                else {},
            )
        )

    return Rubric(
        name=str(raw_rubric.get("name") or fallback_name),
        description=str(
            raw_rubric.get("description")
            or "Defines what good means for this batch training case."
        ),
        criteria=criteria,
        metadata=dict(raw_rubric.get("metadata") or {})
        if isinstance(raw_rubric.get("metadata"), dict)
        else {},
    )


def _case_to_memory_fields(case: Case) -> dict[str, Any]:
    return {
        "case_name": case.name,
        "task_signature": case.task_signature,
        "input": json.dumps(case.input or {}, ensure_ascii=False, sort_keys=True),
        "rubric": json.dumps(_rubric_to_payload(case.rubric), ensure_ascii=False, sort_keys=True),
        "evidence": _case_evidence(case),
    }


def _rubric_to_payload(rubric: Rubric) -> dict[str, Any]:
    return {
        "name": rubric.name,
        "description": rubric.description,
        "criteria": [
            {
                "name": criterion.name,
                "description": criterion.description,
                "required": criterion.required,
                "weight": criterion.weight,
            }
            for criterion in rubric.criteria
        ],
    }


def _case_evidence(case: Case) -> str:
    raw_evidence = (case.metadata or {}).get("evidence")
    if raw_evidence:
        return str(raw_evidence)
    return "Structured batch training CaseSpec supplied by the training pipeline."


def _operations_to_cases(operations: ResolvedOperations) -> list[Case]:
    cases: list[Case] = []
    for op in getattr(operations, "upsert_operations", []) or []:
        if getattr(op, "memory_type", None) != _CASES_MEMORY_TYPE:
            continue
        case = _operation_to_case(op)
        if case is not None:
            cases.append(case)
    return cases


def _operation_to_case(op: ResolvedOperation) -> Case | None:
    fields = dict(getattr(op, "memory_fields", {}) or {})
    name = str(fields.get("case_name") or fields.get("name") or _fallback_case_name(op)).strip()
    task_signature = str(fields.get("task_signature") or name).strip()
    if not name or not task_signature:
        return None
    return Case(
        name=name,
        task_signature=task_signature,
        input=_parse_case_input(fields.get("input")),
        rubric=_parse_rubric(fields.get("rubric"), fallback_name=f"{name}_rubric"),
        metadata={
            "source": "session_commit_case_memory",
            "case_uris": list(getattr(op, "uris", []) or []),
            "evidence": str(fields.get("evidence") or ""),
            "memory_fields": fields,
        },
    )


def _parse_case_input(raw_input: Any) -> dict[str, Any]:
    parsed = _parse_jsonish(raw_input)
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"items": parsed}
    return {"summary": str(raw_input or "")}


def _parse_rubric(raw_rubric: Any, *, fallback_name: str) -> Rubric:
    parsed = _parse_jsonish(raw_rubric)
    raw = parsed if isinstance(parsed, dict) else {}
    raw_criteria = raw.get("criteria") if isinstance(raw.get("criteria"), list) else []
    criteria: list[RubricCriterion] = []
    for index, item in enumerate(raw_criteria):
        if not isinstance(item, dict):
            continue
        criteria.append(
            RubricCriterion(
                name=str(item.get("name") or f"criterion_{index + 1}"),
                description=str(item.get("description") or "The rollout satisfies the task."),
                required=bool(item.get("required", True)),
                weight=_safe_weight(item.get("weight"), default=1.0),
            )
        )
    if not criteria:
        criteria = [
            RubricCriterion(
                name="task_completed",
                description="The assistant completed the user's task with verifiable outcome.",
                required=True,
                weight=1.0,
            )
        ]
    return Rubric(
        name=str(raw.get("name") or fallback_name),
        description=str(raw.get("description") or "Defines what good means for this commit case."),
        criteria=criteria,
    )


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return JsonUtils.loads(value)
    except Exception:
        return None


def _safe_weight(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _first_uri(uris: list[str] | None) -> str | None:
    return uris[0] if uris else None


def _fallback_case_name(op: ResolvedOperation) -> str:
    uri = _first_uri(getattr(op, "uris", []) or [])
    if uri:
        return uri.rstrip("/").split("/")[-1].removesuffix(".md")
    return "commit_case"


def _get_memory_type_from_uri(uri: str) -> str:
    parts = uri.split("/")
    for part in parts:
        if part.endswith(".md"):
            return part.removesuffix(".md")
    return "unknown"


def _experience_root_uri(ctx: RequestContext) -> str:
    user_space = getattr(getattr(ctx, "user", None), "user_id", None) or "default"
    return f"viking://user/{user_space}/memories/experiences"


def _commit_policy_snapshot_id(*, session_id: Optional[str], archive_uri: str) -> str:
    if archive_uri:
        return f"session-commit:{archive_uri.rstrip('/').rsplit('/', 1)[-1]}"
    if session_id:
        return f"session-commit:{session_id}"
    return f"session-commit:{uuid4().hex}"


def _trajectory_content_from_rollout(rollout: Rollout) -> str:
    conversation = "\n".join(
        f"- {message.role}: {message.content}" for message in rollout.messages if message.content
    )
    return "\n".join(
        [
            f"# {rollout.case.name}",
            f"- Task Signature: {rollout.case.task_signature}",
            "- Commit Case: extracted as a case memory from a real session commit.",
            "- Rubric:",
            *[
                f"  - {criterion.name}: {criterion.description}"
                for criterion in rollout.case.rubric.criteria
            ],
            "- Conversation Evidence:",
            conversation,
        ]
    )
