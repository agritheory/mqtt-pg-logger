"""JSON report read/write for journal ingest benchmarks."""

from __future__ import annotations

import json
import platform
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from test.benchmark.stats import summarize, welch_t_test
from typing import Any, cast


@dataclass
class RunMetrics:
	ingest_rate_msg_s: float
	promoted_count: int = 0
	rejected_count: int = 0
	p50_lag_ms: float | None = None
	p95_lag_ms: float | None = None
	elapsed_s: float = 0.0


@dataclass
class LayerReport:
	layer: str
	ingest_mode: str
	messages_per_run: int
	runs: int
	warmup_messages: int
	run_rates: list[float] = field(default_factory=list)
	summary: dict[str, Any] | None = None


@dataclass
class BenchmarkReport:
	generated_at: str
	hostname: str
	platform: str
	ingest_mode: str
	messages_per_run: int
	runs: int
	warmup_messages: int
	layers: dict[str, LayerReport] = field(default_factory=dict)
	comparison: dict[str, Any] | None = None


def layer_report_from_runs(
	layer: str,
	ingest_mode: str,
	messages_per_run: int,
	runs: int,
	warmup_messages: int,
	metrics: list[RunMetrics],
) -> LayerReport:
	rates = [m.ingest_rate_msg_s for m in metrics]
	stats = summarize(rates)
	lag_p50 = [m.p50_lag_ms for m in metrics if m.p50_lag_ms is not None]
	lag_p95 = [m.p95_lag_ms for m in metrics if m.p95_lag_ms is not None]
	summary: dict[str, Any] = {
		"ingest_rate_msg_s": asdict(stats),
		"promoted_total": sum(m.promoted_count for m in metrics),
		"rejected_total": sum(m.rejected_count for m in metrics),
	}
	if lag_p50:
		summary["p50_lag_ms"] = asdict(summarize(lag_p50))
	if lag_p95:
		summary["p95_lag_ms"] = asdict(summarize(lag_p95))
	return LayerReport(
		layer=layer,
		ingest_mode=ingest_mode,
		messages_per_run=messages_per_run,
		runs=runs,
		warmup_messages=warmup_messages,
		run_rates=rates,
		summary=summary,
	)


def build_report(
	ingest_mode: str,
	messages_per_run: int,
	runs: int,
	warmup_messages: int,
	layer_metrics: dict[str, list[RunMetrics]],
) -> BenchmarkReport:
	layers = {
		name: layer_report_from_runs(name, ingest_mode, messages_per_run, runs, warmup_messages, metrics)
		for name, metrics in layer_metrics.items()
	}
	return BenchmarkReport(
		generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
		hostname=socket.gethostname(),
		platform=platform.platform(),
		ingest_mode=ingest_mode,
		messages_per_run=messages_per_run,
		runs=runs,
		warmup_messages=warmup_messages,
		layers=layers,
	)


def report_to_dict(report: BenchmarkReport) -> dict[str, Any]:
	layers_dict = {}
	for name, layer in report.layers.items():
		layers_dict[name] = {
			"layer": layer.layer,
			"ingest_mode": layer.ingest_mode,
			"messages_per_run": layer.messages_per_run,
			"runs": layer.runs,
			"warmup_messages": layer.warmup_messages,
			"run_rates": layer.run_rates,
			"summary": layer.summary,
		}
	return {
		"generated_at": report.generated_at,
		"hostname": report.hostname,
		"platform": report.platform,
		"ingest_mode": report.ingest_mode,
		"messages_per_run": report.messages_per_run,
		"runs": report.runs,
		"warmup_messages": report.warmup_messages,
		"layers": layers_dict,
		"comparison": report.comparison,
	}


def write_report(path: Path, report: BenchmarkReport) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(report_to_dict(report), indent=2), encoding="utf-8")


def load_report(path: Path) -> dict[str, Any]:
	return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def compare_to_baseline(
	report: BenchmarkReport,
	baseline_path: Path,
) -> dict[str, Any]:
	baseline = load_report(baseline_path)
	comparison: dict[str, Any] = {}
	for layer_name, layer in report.layers.items():
		base_layer = baseline.get("layers", {}).get(layer_name)
		if not base_layer:
			continue
		base_rates = base_layer.get("run_rates", [])
		t_stat, p_value = welch_t_test(layer.run_rates, base_rates)
		current_stats = summarize(layer.run_rates)
		base_stats = summarize(base_rates)
		speedup = current_stats.mean / base_stats.mean if base_stats.mean else 0.0
		comparison[layer_name] = {
			"baseline_mean": base_stats.mean,
			"current_mean": current_stats.mean,
			"speedup": speedup,
			"welch_t": t_stat,
			"p_value": p_value,
			"significant_at_0_05": p_value < 0.05,
			"baseline_ci95": [base_stats.ci95_low, base_stats.ci95_high],
			"current_ci95": [current_stats.ci95_low, current_stats.ci95_high],
		}
	report.comparison = comparison
	return comparison
