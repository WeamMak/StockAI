"""Current-run Prometheus proof helpers."""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest
from tests.smoke import test_dev_skeleton as smoke
from tests.smoke.test_dev_skeleton import (
    METRIC_QUERIES,
    _metric_total,
    _targets_are_up,
    _wait_for_metric_deltas,
)


def _payload(*values: str, jobs: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "data": {
            "result": [
                {
                    "metric": {"job": jobs[index]} if jobs else {},
                    "value": [0, value],
                }
                for index, value in enumerate(values)
            ]
        }
    }


def test_metric_total_treats_absent_series_as_zero_and_sums_replicas() -> None:
    assert _metric_total({"data": {"result": []}}) == 0.0
    assert _metric_total(_payload("2", "3")) == 5.0


def test_target_health_requires_both_jobs_and_every_target_up() -> None:
    jobs = ("stockai-agent-api", "stockai-procurement-mcp")

    assert _targets_are_up(_payload("1", "1", jobs=jobs)) is True
    assert _targets_are_up(_payload("1", "0", jobs=jobs)) is False
    assert _targets_are_up(_payload("1", jobs=jobs[:1])) is False


def test_metric_delta_wait_times_out_with_missing_query_names() -> None:
    query = METRIC_QUERIES[0]

    with pytest.raises(AssertionError, match="procurement_llm_calls_total"):
        _wait_for_metric_deltas(
            cast(httpx.Client, object()),
            {query: 1.0},
            timeout_seconds=0,
            poll_seconds=0,
        )


def test_metric_delta_wait_accepts_only_new_counts_and_healthy_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baselines = {query: 4.0 for query in METRIC_QUERIES}

    def query_metric(
        _client: object,
        _datasource: str,
        _path: str,
        params: dict[str, str],
    ) -> dict[str, Any]:
        if params["query"] == smoke.TARGET_HEALTH_QUERY:
            return _payload(
                "1",
                "1",
                jobs=("stockai-agent-api", "stockai-procurement-mcp"),
            )
        return _payload("5")

    monkeypatch.setattr(smoke, "_grafana_query", query_metric)

    _wait_for_metric_deltas(
        cast(httpx.Client, object()),
        baselines,
        timeout_seconds=1,
        poll_seconds=0,
    )
