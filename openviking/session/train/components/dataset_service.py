# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Generic HTTP service host for remote benchmark datasets."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from openviking.session.train.context import ExecutionContext
from openviking.session.train.domain import (
    Case,
    ExperienceSet,
    Rollout,
    Rubric,
    RubricCriterion,
    RubricEvaluation,
)


CaseLoaderFactory = Callable[[str, str, str, dict[str, Any]], Any]
RolloutExecutorFactory = Callable[[dict[str, Any]], Any]
logger = logging.getLogger(__name__)


class CasesQueryRequest(BaseModel):
    dataset: str
    domain: str
    split: str
    cursor: str | None = None
    limit: int = Field(default=100, gt=0)
    filters: dict[str, Any] = Field(default_factory=dict)


class RolloutExecuteRequest(BaseModel):
    case: dict[str, Any]
    policy_set: dict[str, Any]
    execution_context: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class RolloutExecution:
    execution_id: str
    status: str
    created_at: float
    updated_at: float
    case_name: str
    rollout: Rollout | None = None
    error: str | None = None


class RolloutExecutionStore:
    def __init__(self) -> None:
        self._executions: dict[str, RolloutExecution] = {}
        self._lock = asyncio.Lock()

    async def create(self, *, case_name: str) -> RolloutExecution:
        now = time.time()
        execution = RolloutExecution(
            execution_id=f"rollout_exec_{uuid4().hex}",
            status="running",
            created_at=now,
            updated_at=now,
            case_name=case_name,
        )
        async with self._lock:
            self._executions[execution.execution_id] = execution
        return execution

    async def get(self, execution_id: str) -> RolloutExecution | None:
        async with self._lock:
            return self._executions.get(execution_id)

    async def count_by_status(self) -> dict[str, int]:
        async with self._lock:
            counts: dict[str, int] = {}
            for execution in self._executions.values():
                counts[execution.status] = counts.get(execution.status, 0) + 1
            return counts

    async def mark_completed(self, execution_id: str, rollout: Rollout) -> None:
        await self._update(execution_id, status="completed", rollout=rollout)

    async def mark_failed(self, execution_id: str, error: str) -> None:
        await self._update(execution_id, status="failed", error=error)

    async def _update(self, execution_id: str, **changes: Any) -> None:
        async with self._lock:
            execution = self._executions[execution_id]
            for key, value in changes.items():
                setattr(execution, key, value)
            execution.updated_at = time.time()


def create_dataset_service_app(
    *,
    service_name: str,
    make_case_loader: CaseLoaderFactory,
    make_rollout_executor: RolloutExecutorFactory,
    max_rollout_concurrency: int | None = None,
) -> FastAPI:
    """Create a generic remote dataset service from train framework components."""

    if max_rollout_concurrency is not None and max_rollout_concurrency <= 0:
        raise ValueError("max_rollout_concurrency must be > 0")

    app = FastAPI(title=f"OpenViking {service_name} Dataset Service")
    app.state.service_name = service_name
    app.state.make_case_loader = make_case_loader
    app.state.make_rollout_executor = make_rollout_executor
    app.state.rollout_executions = RolloutExecutionStore()
    app.state.max_rollout_concurrency = max_rollout_concurrency
    app.state.rollout_semaphore = (
        asyncio.Semaphore(max_rollout_concurrency)
        if max_rollout_concurrency is not None
        else None
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": app.state.service_name,
            "max_rollout_concurrency": app.state.max_rollout_concurrency,
            "rollout_executions": await app.state.rollout_executions.count_by_status(),
        }

    @app.post("/v1/cases/query")
    async def query_cases(request: CasesQueryRequest) -> dict[str, Any]:
        loader = app.state.make_case_loader(
            request.dataset,
            request.domain,
            request.split,
            dict(request.filters or {}),
        )
        cases = await _load_case_page(
            loader,
            cursor=request.cursor,
            limit=request.limit,
        )
        next_offset = int(request.cursor or "0") + len(cases)
        next_cursor = str(next_offset) if len(cases) >= request.limit else None
        return {
            "cases": [case_to_dict(case) for case in cases],
            "next_cursor": next_cursor,
        }

    @app.post("/v1/rollouts/execute")
    async def execute_rollout(request: RolloutExecuteRequest) -> dict[str, Any]:
        case = case_from_dict(request.case)
        execution = await app.state.rollout_executions.create(case_name=case.name)
        asyncio.create_task(_run_rollout_execution(app, execution.execution_id, request))
        return execution_to_dict(execution)

    @app.get("/v1/rollouts/executions/{execution_id}")
    async def get_rollout_execution(execution_id: str) -> dict[str, Any]:
        execution = await app.state.rollout_executions.get(execution_id)
        if execution is None:
            raise HTTPException(
                status_code=404,
                detail=f"Rollout execution not found: {execution_id}",
            )
        return execution_to_dict(execution)

    return app


async def _run_rollout_execution(
    app: FastAPI,
    execution_id: str,
    request: RolloutExecuteRequest,
) -> None:
    case = case_from_dict(request.case)
    try:
        semaphore = app.state.rollout_semaphore
        if semaphore is None:
            rollout = await _execute_rollout_request(app, request, case)
        else:
            async with semaphore:
                rollout = await _execute_rollout_request(app, request, case)
        await app.state.rollout_executions.mark_completed(execution_id, rollout)
    except Exception as exc:
        logger.exception(
            "rollout execution failed execution_id=%s case=%s",
            execution_id,
            case.name,
        )
        await app.state.rollout_executions.mark_failed(execution_id, str(exc))


async def _execute_rollout_request(
    app: FastAPI,
    request: RolloutExecuteRequest,
    case: Case,
) -> Rollout:
    options = dict(request.options or {})
    executor = app.state.make_rollout_executor(options)
    rollouts = await executor.execute(
        [case],
        policy_set_from_dict(request.policy_set),
        ExecutionContext(
            policy_snapshot_id=str(request.execution_context["policy_snapshot_id"]),
            metadata=dict(request.execution_context.get("metadata") or {}),
        ),
    )
    return rollouts[0]


async def _load_case_page(loader: Any, *, cursor: str | None, limit: int) -> list[Case]:
    offset = int(cursor or "0")
    selected: list[Case] = []
    seen = 0
    async for batch in loader.batches(None):
        for case in batch:
            if seen < offset:
                seen += 1
                continue
            if len(selected) >= limit:
                return selected
            selected.append(case)
            seen += 1
    return selected


def execution_to_dict(execution: RolloutExecution) -> dict[str, Any]:
    data: dict[str, Any] = {
        "execution_id": execution.execution_id,
        "status": execution.status,
        "case_name": execution.case_name,
        "created_at": execution.created_at,
        "updated_at": execution.updated_at,
        "error": execution.error,
    }
    if execution.rollout is not None:
        data["rollout"] = rollout_to_dict(execution.rollout)
    return data


def case_to_dict(case: Case) -> dict[str, Any]:
    return {
        "name": case.name,
        "task_signature": case.task_signature,
        "input": case.input,
        "rubric": {
            "name": case.rubric.name,
            "description": case.rubric.description,
            "criteria": [
                {
                    "name": criterion.name,
                    "description": criterion.description,
                    "required": criterion.required,
                    "weight": criterion.weight,
                    "metadata": criterion.metadata,
                }
                for criterion in case.rubric.criteria
            ],
            "metadata": case.rubric.metadata,
        },
        "metadata": case.metadata,
    }


def case_from_dict(data: dict[str, Any]) -> Case:
    rubric = data["rubric"]
    return Case(
        name=data["name"],
        task_signature=data["task_signature"],
        input=dict(data.get("input") or {}),
        rubric=Rubric(
            name=rubric["name"],
            description=rubric.get("description", ""),
            criteria=[
                RubricCriterion(
                    name=item["name"],
                    description=item.get("description", ""),
                    required=bool(item.get("required", True)),
                    weight=float(item.get("weight", 1.0)),
                    metadata=dict(item.get("metadata") or {}),
                )
                for item in rubric.get("criteria", [])
            ],
            metadata=dict(rubric.get("metadata") or {}),
        ),
        metadata=dict(data.get("metadata") or {}),
    )


def policy_set_from_dict(data: dict[str, Any]) -> ExperienceSet:
    return ExperienceSet(
        root_uri=data["root_uri"],
        policies=[],
        metadata=dict(data.get("metadata") or {}),
    )


def rollout_to_dict(rollout: Rollout) -> dict[str, Any]:
    return {
        "case": case_to_dict(rollout.case),
        "messages": [message.to_dict() for message in rollout.messages],
        "policy_snapshot_id": rollout.policy_snapshot_id,
        "evaluation": jsonable(evaluation_to_dict(rollout.evaluation)),
        "metadata": jsonable(rollout.metadata),
    }


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(jsonable(key)): jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(item) for item in value]
    return value


def evaluation_to_dict(evaluation: RubricEvaluation | None) -> dict[str, Any] | None:
    if evaluation is None:
        return None
    return {
        "passed": evaluation.passed,
        "score": evaluation.score,
        "criterion_results": [
            {
                "criterion_name": result.criterion_name,
                "passed": result.passed,
                "score": result.score,
                "feedback": result.feedback,
                "evidence": result.evidence,
                "metadata": result.metadata,
            }
            for result in evaluation.criterion_results
        ],
        "feedback": evaluation.feedback,
        "metadata": evaluation.metadata,
    }
