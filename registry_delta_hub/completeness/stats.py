"""Small, dependency-free statistical helpers used by the completeness analysis.

The project intentionally relies on the Python standard library only.  These
routines are standard textbook implementations; they are deliberately kept in
one module so that every interval reported elsewhere points at the same code.
"""
from __future__ import annotations

import math


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz method)."""
    max_iterations = 300
    epsilon = 3.0e-14
    tiny = 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return h


def beta_cdf(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_beta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(log_beta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def beta_quantile(q: float, a: float, b: float, tolerance: float = 1.0e-10) -> float:
    """Inverse of :func:`beta_cdf` by bisection (a, b are positive floats)."""
    if q <= 0.0:
        return 0.0
    if q >= 1.0:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if beta_cdf(middle, a, b) < q:
            low = middle
        else:
            high = middle
        if high - low < tolerance:
            break
    return (low + high) / 2.0


def clopper_pearson(x: float, n: float, confidence: float = 0.95) -> tuple[float, float]:
    """Exact binomial interval allowing fractional counts (effective sizes).

    ``x`` and ``n`` may be floats so that design-effect adjusted effective
    sample sizes can be plugged in.  This is the usual Korn-Graubard style
    approximation used for weighted proportions.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if x < 0 or x > n:
        raise ValueError("x must lie in [0, n]")
    alpha = 1.0 - confidence
    lower = 0.0 if x <= 0 else beta_quantile(alpha / 2.0, x, n - x + 1.0)
    upper = 1.0 if x >= n else beta_quantile(1.0 - alpha / 2.0, x + 1.0, n - x)
    return lower, upper


def normal_quantile(p: float) -> float:
    """Inverse standard normal CDF via bisection on ``math.erf``."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must lie in (0, 1)")
    low, high = -10.0, 10.0
    for _ in range(200):
        middle = (low + high) / 2.0
        cdf = 0.5 * (1.0 + math.erf(middle / math.sqrt(2.0)))
        if cdf < p:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def effective_sample_size(weights: list[float]) -> float:
    """Kish effective sample size for a set of survey weights."""
    total = sum(weights)
    squares = sum(weight * weight for weight in weights)
    if squares <= 0:
        return 0.0
    return total * total / squares


def beta_binomial_pmf(successes: int, trials: int, a: float, b: float) -> float:
    """Beta-binomial predictive probability for ``successes`` out of ``trials``."""
    log_choose = (
        math.lgamma(trials + 1)
        - math.lgamma(successes + 1)
        - math.lgamma(trials - successes + 1)
    )
    log_probability = (
        log_choose
        + math.lgamma(successes + a)
        + math.lgamma(trials - successes + b)
        - math.lgamma(trials + a + b)
        + math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
    )
    return math.exp(log_probability)
