"""Tests for LoCoMo benchmark progress utilities."""

from rich.progress import ProgressColumn

from benchmark.locomo.vikingbot.progress_utils import ThreeStateBarColumn, make_three_state_progress


def test_three_state_bar_column_initializes_progress_column_state():
    column = ThreeStateBarColumn()

    assert isinstance(column, ProgressColumn)
    assert hasattr(column, "_renderable_cache")
    assert hasattr(column, "_update_time")


def test_three_state_progress_renders_without_missing_cache_error():
    progress, task_id = make_three_state_progress(description="Test", transient=True)
    progress.update(task_id, total=3, completed=1, running=1)

    renderables = list(progress.get_renderables())

    assert renderables
