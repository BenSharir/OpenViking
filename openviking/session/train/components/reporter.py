# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Console reporting helpers for session training pipelines."""

from __future__ import annotations

import inspect
import sys
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol

try:  # pragma: no cover - cosmetic terminal rendering
    from rich.console import Console
    from rich.text import Text
except Exception:  # pragma: no cover - rich is optional
    Console = None
    Text = None

from openviking.session.train.components.progress import format_duration, format_label, label_style


HookResult = Awaitable[None] | None
ReportHookResult = Awaitable[dict[str, Any] | None] | dict[str, Any] | None
DecisionHookResult = Awaitable[Any] | Any | None


class PipelineLifecycleHook(Protocol):
    """Lifecycle hook extension point for train/eval pipelines."""

    def on_epoch_start(self, *, epoch: int, context: Any) -> HookResult: ...

    def on_train_rollout_end(
        self,
        *,
        epoch: int,
        rollouts: list[Any],
        snapshot_id: str,
        policy_set: Any,
        context: Any,
    ) -> ReportHookResult: ...

    def on_epoch_end(
        self,
        *,
        epoch_result: Any,
        policy_set: Any,
        context: Any,
    ) -> DecisionHookResult: ...

    def on_eval_end(
        self,
        *,
        evaluation_result: Any,
        policy_set: Any,
        context: Any,
    ) -> ReportHookResult: ...

    def on_eval_report(
        self,
        *,
        label: str,
        report: dict[str, Any],
        context: Any,
    ) -> HookResult: ...

    def on_train_rollout_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> HookResult: ...

    def on_train_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> HookResult: ...

    def on_run_summary(
        self,
        *,
        title: str,
        fields: dict[str, Any],
        baseline_eval: dict[str, Any] | None = None,
        final_eval: dict[str, Any] | None = None,
        accuracy_delta: float | None = None,
        output_path: str | None = None,
        rollouts_root: str | None = None,
        rollouts_index_path: str | None = None,
        latest_failed_rollout: str | None = None,
    ) -> HookResult: ...


class NoopPipelineLifecycleHook:
    """Base class for lifecycle hooks that only need to override some events."""

    def on_epoch_start(self, *, epoch: int, context: Any) -> None:
        del epoch, context

    def on_train_rollout_end(
        self,
        *,
        epoch: int,
        rollouts: list[Any],
        snapshot_id: str,
        policy_set: Any,
        context: Any,
    ) -> None:
        del epoch, rollouts, snapshot_id, policy_set, context

    def on_epoch_end(
        self,
        *,
        epoch_result: Any,
        policy_set: Any,
        context: Any,
    ) -> None:
        del epoch_result, policy_set, context

    def on_eval_end(
        self,
        *,
        evaluation_result: Any,
        policy_set: Any,
        context: Any,
    ) -> None:
        del evaluation_result, policy_set, context

    def on_eval_report(
        self,
        *,
        label: str,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del label, report, context

    def on_train_rollout_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del report, context

    def on_train_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del report, context

    def on_run_summary(
        self,
        *,
        title: str,
        fields: dict[str, Any],
        baseline_eval: dict[str, Any] | None = None,
        final_eval: dict[str, Any] | None = None,
        accuracy_delta: float | None = None,
        output_path: str | None = None,
        rollouts_root: str | None = None,
        rollouts_index_path: str | None = None,
        latest_failed_rollout: str | None = None,
    ) -> None:
        del (
            title,
            fields,
            baseline_eval,
            final_eval,
            accuracy_delta,
            output_path,
            rollouts_root,
            rollouts_index_path,
            latest_failed_rollout,
        )


async def emit_run_summary(
    context: Any,
    *,
    title: str,
    fields: dict[str, Any],
    baseline_eval: dict[str, Any] | None = None,
    final_eval: dict[str, Any] | None = None,
    accuracy_delta: float | None = None,
    output_path: str | None = None,
    rollouts_root: str | None = None,
    rollouts_index_path: str | None = None,
    latest_failed_rollout: str | None = None,
) -> None:
    """Emit a run-level summary event to lifecycle hooks on a pipeline context."""

    lifecycle_hooks = list(getattr(context, "lifecycle_hooks", []) or [])
    for hook in lifecycle_hooks:
        result = hook.on_run_summary(
            title=title,
            fields=fields,
            baseline_eval=baseline_eval,
            final_eval=final_eval,
            accuracy_delta=accuracy_delta,
            output_path=output_path,
            rollouts_root=rollouts_root,
            rollouts_index_path=rollouts_index_path,
            latest_failed_rollout=latest_failed_rollout,
        )
        if inspect.isawaitable(result):
            await result


@dataclass(slots=True)
class ConsolePipelineReporter(NoopPipelineLifecycleHook):
    """Default stdout lifecycle hook for batch train/eval runners."""

    use_rich: bool | None = None

    def __post_init__(self) -> None:
        if self.use_rich is None:
            self.use_rich = Console is not None and Text is not None and sys.stdout.isatty()

    def on_eval_report(
        self,
        *,
        label: str,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del context
        label = str(report.get("rollout_stage") or label)
        split = report.get("split")
        trial_count = int(report.get("trial_count") or 1)
        if trial_count > 1:
            self._print_line(
                label,
                [
                    ("epoch", report["epoch"]),
                    *_split_field(split),
                    ("trials", trial_count, "cyan"),
                    ("cases_per_trial", report.get("case_count_per_trial") or "varies"),
                    (
                        "total_rollouts",
                        report.get("total_rollout_count", report["case_count"]),
                        "cyan",
                    ),
                    (
                        "accuracy",
                        fmt_percent(report.get("accuracy_mean")),
                        _accuracy_style(report.get("accuracy_mean")),
                    ),
                    ("", f"± {fmt_percentage_point_abs(report.get('accuracy_std'))}", "yellow"),
                    ("avg_reward", fmt_score(report.get("average_reward_mean")), "bold"),
                    ("", f"± {fmt_score(report.get('average_reward_std'))}", "yellow"),
                    *_cost_field(report),
                ],
            )
            return
        self._print_line(
            label,
            [
                ("epoch", report["epoch"]),
                *_split_field(split),
                ("cases", report["case_count"]),
                (
                    "accuracy",
                    fmt_percent(report["accuracy"]),
                    _accuracy_style(report.get("accuracy")),
                ),
                (
                    "passed",
                    f"{report['passed_count']}/{report['case_count']}",
                    _passed_style(report),
                ),
                ("avg_reward", fmt_score(report["average_reward"]), "bold"),
                *_cost_field(report),
            ],
        )

    def on_epoch_start(self, *, epoch: int, context: Any) -> None:
        del context
        text = f" epoch {epoch} "
        width = 44
        left = max((width - len(text)) // 2, 1)
        right = max(width - len(text) - left, 1)
        line = f"{'=' * left}{text}{'=' * right}"
        if not self.use_rich:
            print(line)
            return
        Console().print(line, style="bold cyan")

    def on_train_rollout_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del context
        self._print_line(
            "train_rollout",
            [
                ("epoch", report["epoch"]),
                ("cases", report["case_count"]),
                (
                    "accuracy",
                    fmt_percent(report["accuracy"]),
                    _accuracy_style(report.get("accuracy")),
                ),
                (
                    "passed",
                    f"{report['passed_count']}/{report['case_count']}",
                    _passed_style(report),
                ),
                ("avg_reward", fmt_score(report["average_reward"]), "bold"),
                *_cost_field(report),
            ],
        )

    def on_train_report(
        self,
        *,
        report: dict[str, Any],
        context: Any,
    ) -> None:
        del context
        error_count = len(report["errors"])
        self._print_line(
            "train",
            [
                ("epoch", report["epoch"]),
                ("commits", report["committed_rollout_count"], "cyan"),
                ("errors", error_count, "green" if error_count == 0 else "red bold"),
                *_cost_field(report),
            ],
        )
        if report.get("errors"):
            trace_ids = report.get("failed_commit_trace_ids") or []
            telemetry_ids = report.get("failed_commit_telemetry_ids") or []
            if trace_ids:
                print(f"[train] failed_commit_trace_ids={','.join(trace_ids)}")
            else:
                print("[train] failed_commit_trace_ids=<none>")
            if telemetry_ids:
                print(f"[train] failed_commit_telemetry_ids={','.join(telemetry_ids)}")

    def on_run_summary(
        self,
        *,
        title: str,
        fields: dict[str, Any],
        baseline_eval: dict[str, Any] | None = None,
        final_eval: dict[str, Any] | None = None,
        accuracy_delta: float | None = None,
        output_path: str | None = None,
        rollouts_root: str | None = None,
        rollouts_index_path: str | None = None,
        latest_failed_rollout: str | None = None,
    ) -> None:
        print(f"==== {title} ====")
        for key, value in fields.items():
            if value is not None:
                print(f"{key}: {value}")
        if baseline_eval:
            self._report_eval_line("baseline", baseline_eval)
        if final_eval:
            self._report_eval_line("final", final_eval)
        if accuracy_delta is not None:
            print(f"accuracy delta: {fmt_percentage_point(accuracy_delta)}")
        if output_path:
            print(f"report: {output_path}")
        if rollouts_root:
            print(f"rollouts: {rollouts_root}")
        if rollouts_index_path:
            print(f"rollouts_index: {rollouts_index_path}")
        if latest_failed_rollout:
            print(f"latest_failed_rollout: {latest_failed_rollout}")

    def _print_line(self, label: str, fields: list[tuple[Any, ...]]) -> None:
        if not self.use_rich:
            print(
                f"[{label}] "
                + " ".join(
                    f"{item[0]}={item[1]}" if item[0] else str(item[1])
                    for item in fields
                )
            )
            return
        console = Console()
        line = Text()
        line.append(format_label(label), style=label_style(label))
        for item in fields:
            key = str(item[0])
            value = str(item[1])
            value_style = str(item[2]) if len(item) > 2 else "default"
            line.append(" ")
            if key:
                line.append(f"{key}=", style="dim")
            line.append(value, style=value_style)
        console.print(line)

    def _report_eval_line(self, label: str, data: dict[str, Any]) -> None:
        trial_count = int(data.get("trial_count") or 1)
        if trial_count > 1:
            print(
                f"{label} accuracy: {fmt_percent(data.get('accuracy_mean'))} ± "
                f"{fmt_percentage_point_abs(data.get('accuracy_std'))} "
                f"(trials={trial_count}, "
                f"cases_per_trial={data.get('case_count_per_trial') or 'varies'})"
            )
            print(
                f"{label} average reward: {fmt_score(data.get('average_reward_mean'))} ± "
                f"{fmt_score(data.get('average_reward_std'))}"
            )
            return
        print(
            f"{label} accuracy: "
            f"{fmt_percent(data['accuracy'])} "
            f"({data['passed_count']}/{data['case_count']})"
        )
        print(f"{label} average reward: {fmt_score(data['average_reward'])}")


def _cost_field(report: dict[str, Any]) -> list[tuple[str, str, str]]:
    cost_seconds = report.get("cost_seconds")
    if cost_seconds is None:
        return []
    return [("cost", format_duration(float(cost_seconds)), "magenta bold")]


def _split_field(split: Any) -> list[tuple[str, str, str]]:
    if split is None:
        return []
    return [("split", str(split), "cyan")]


def fmt_score(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.6f}"


def fmt_percent(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def fmt_percentage_point(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:+.2f}pp"


def fmt_percentage_point_abs(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}pp"


def _accuracy_style(value: Any) -> str:
    if value is None:
        return "dim"
    score = float(value)
    if score >= 0.8:
        return "green bold"
    if score >= 0.5:
        return "yellow bold"
    return "red bold"


def _passed_style(data: dict[str, Any]) -> str:
    case_count = int(data.get("case_count") or 0)
    passed_count = int(data.get("passed_count") or 0)
    if case_count > 0 and passed_count == case_count:
        return "green bold"
    if passed_count == 0:
        return "red bold"
    return "yellow bold"
