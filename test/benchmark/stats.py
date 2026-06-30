"""Statistics helpers for journal ingest benchmarks."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SummaryStats:
	n: int
	mean: float
	stdev: float
	p50: float
	p95: float
	ci95_low: float
	ci95_high: float


def mean(values: Sequence[float]) -> float:
	if not values:
		return 0.0
	return sum(values) / len(values)


def stdev(values: Sequence[float]) -> float:
	if len(values) < 2:
		return 0.0
	m = mean(values)
	variance = sum((v - m) ** 2 for v in values) / (len(values) - 1)
	return math.sqrt(variance)


def percentile(values: Sequence[float], pct: float) -> float:
	if not values:
		return 0.0
	sorted_values = sorted(values)
	index = (len(sorted_values) - 1) * pct
	lower = math.floor(index)
	upper = math.ceil(index)
	if lower == upper:
		return sorted_values[lower]
	weight = index - lower
	return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def confidence_interval_95(values: Sequence[float]) -> tuple[float, float]:
	if len(values) < 2:
		m = mean(values)
		return m, m
	m = mean(values)
	s = stdev(values)
	margin = 1.96 * s / math.sqrt(len(values))
	return m - margin, m + margin


def summarize(values: Sequence[float]) -> SummaryStats:
	ci_low, ci_high = confidence_interval_95(values)
	return SummaryStats(
		n=len(values),
		mean=mean(values),
		stdev=stdev(values),
		p50=percentile(values, 0.50),
		p95=percentile(values, 0.95),
		ci95_low=ci_low,
		ci95_high=ci_high,
	)


def welch_t_test(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
	"""Return (t_statistic, p_value_approx) for unequal-variance two-sample t-test."""
	if len(a) < 2 or len(b) < 2:
		return 0.0, 1.0

	m_a, m_b = mean(a), mean(b)
	s_a, s_b = stdev(a), stdev(b)
	n_a, n_b = len(a), len(b)

	se = math.sqrt((s_a**2 / n_a) + (s_b**2 / n_b))
	if se == 0:
		return 0.0, 1.0

	t_stat = (m_a - m_b) / se

	# Welch–Satterthwaite degrees of freedom
	num = (s_a**2 / n_a + s_b**2 / n_b) ** 2
	den = (s_a**2 / n_a) ** 2 / (n_a - 1) + (s_b**2 / n_b) ** 2 / (n_b - 1)
	df = num / den if den else 1.0

	p_value = 2 * (1 - student_t_cdf(abs(t_stat), df))
	return t_stat, p_value


def student_t_cdf(t: float, df: float) -> float:
	"""Approximate two-sided Student-t CDF via regularized incomplete beta."""
	x = df / (df + t * t)
	a = df / 2
	b = 0.5
	return 0.5 * regularized_incomplete_beta(x, a, b)


def regularized_incomplete_beta(x: float, a: float, b: float) -> float:
	if x <= 0:
		return 0.0
	if x >= 1:
		return 1.0

	ln_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
	front = math.exp(math.log(x) * a + math.log(1 - x) * b - ln_beta) / a

	if x < (a + 1) / (a + b + 2):
		return front * beta_continued_fraction(x, a, b)
	return 1 - (
		math.exp(math.log(1 - x) * b + math.log(x) * a - ln_beta) / b
	) * beta_continued_fraction(1 - x, b, a)


def beta_continued_fraction(x: float, a: float, b: float) -> float:
	max_iterations = 200
	epsilon = 3e-7
	am, bm = 1.0, 1.0
	az = 1.0
	qab = a + b
	qap = a + 1
	qam = a - 1
	bz = 1 - qab * x / qap

	for m in range(1, max_iterations + 1):
		em = float(m)
		tem = em + em
		d = em * (b - em) * x / ((qam + tem) * (a + tem))
		ap = az + d * am
		bp = bz + d * bm
		d = -(a + em) * (qab + em) * x / ((a + tem) * (qap + tem))
		app = ap + d * az
		bpp = bp + d * bz
		am, az = ap, app
		bm, bz = bp, bpp
		if abs(bz) < epsilon:
			return az / bz
		aold = az
		az = az / bz
		bz = 1.0
		if abs(az - aold) < epsilon * abs(az):
			return az
	return az
