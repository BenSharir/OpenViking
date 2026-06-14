#!/usr/bin/env python3
"""HTTP service exposing tau2 cases and rollout execution."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark.tau2.train.case_loader import Tau2CaseLoader
from benchmark.tau2.train.rollout_executor import (
    DEFAULT_TAU2_ROLLOUT_BACKEND,
    make_tau2_rollout_executor,
    normalize_tau2_rollout_backend,
)
from openviking.session.train.components.dataset_service import create_dataset_service_app


def create_app(
    *,
    data_root: str | None = None,
    config_path: str | None = None,
    rollout_language: str = "default",
    rollout_backend: str | None = None,
):
    if rollout_language not in {"default", "zh"}:
        raise ValueError("rollout_language must be 'default' or 'zh'")
    default_backend = normalize_tau2_rollout_backend(
        rollout_backend or os.getenv("TAU2_ROLLOUT_BACKEND") or DEFAULT_TAU2_ROLLOUT_BACKEND
    )

    def make_case_loader(
        dataset: str,
        domain: str,
        split: str,
        filters: dict[str, Any],
    ) -> Tau2CaseLoader:
        del filters
        if dataset != "tau2":
            raise ValueError(f"Unsupported dataset: {dataset}")
        return Tau2CaseLoader(
            domain=domain,
            split=split,
            data_root=data_root,
        )

    def make_rollout_executor(options: dict[str, Any]):
        backend = normalize_tau2_rollout_backend(
            options.get("rollout_backend")
            or options.get("backend")
            or default_backend
        )
        return make_tau2_rollout_executor(
            backend=backend,
            options=options,
            config_path=config_path,
            concurrency=1,
            rollout_language=rollout_language,
        )

    return create_dataset_service_app(
        service_name="tau2",
        make_case_loader=make_case_loader,
        make_rollout_executor=make_rollout_executor,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start tau2 rollout HTTP service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1944)
    parser.add_argument("--data-root", default=os.getenv("TAU2_DATA_ROOT"))
    parser.add_argument("--config", default=os.getenv("OPENVIKING_CONFIG_FILE"))
    parser.add_argument("--rollout-language", choices=["default", "zh"], default="default")
    parser.add_argument(
        "--rollout-backend",
        choices=["native", "vikingbot"],
        default=os.getenv("TAU2_ROLLOUT_BACKEND", DEFAULT_TAU2_ROLLOUT_BACKEND),
        help="Rollout implementation backend (default: native).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    import uvicorn

    uvicorn.run(
        create_app(
            data_root=args.data_root,
            config_path=args.config,
            rollout_language=args.rollout_language,
            rollout_backend=args.rollout_backend,
        ),
        host=args.host,
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
