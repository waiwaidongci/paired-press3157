"""Sequential monitoring statistics for paired press differences.

Under H0 each difference is independent and symmetric about zero, so its sign
is an independent fair coin (Rademacher).  We use the Beta(1/2, 1/2) predictive
betting martingale (a.k.a. the universal-portfolio / testing-by-betting
e-process for a sign):

    M_0 = 1
    p_i(+) = (k_{i-1} + 1/2) / (nz_{i-1} + 1)
    p_i(-) = (nz_{i-1} - k_{i-1} + 1/2) / (nz_{i-1} + 1)
    M_i = M_{i-1} * 2 * p_i(S_i)

where nz is the number of non-zero differences seen before step i and k is the
number of positive signs among them.  Conditional on the past,
E[2 p_i(S_i)] = p_i(+) + p_i(-) = 1, so M is a non-negative martingale with
E[M_0] = 1.  Ville's inequality gives, for a locked threshold 1/alpha,

    P( exists n : M_n >= 1/alpha ) <= alpha

for every (predictable) sequence of looks and every stopping rule.  With
alpha = 0.05 the locked threshold is 20.  The process is two-sided: a run of
positive signs and a run of negative signs both increase the wealth.

The closed form is

    M_n = 2^nz * B(k + 1/2, nz - k + 1/2) / B(1/2, 1/2)

and is used by tests to check the per-sample updates.  A zero difference
contributes no evidence (its sign is uninformative and has probability zero
under a continuous symmetric null), but it is still recorded in the inclusion
scope.
"""

import math
from math import lgamma

ALPHA = 0.05
THRESHOLD = 1.0 / ALPHA
LOG_THRESHOLD = math.log(THRESHOLD)
METHOD = "双侧Ville e过程（Beta(1/2,1/2)符号预测市场）"
BASIS = (
    "零假设下各配对差独立且关于零对称，故差值的符号是独立公平的±1。"
    "按Beta(1/2,1/2)后验预测概率下注，得到非负鞅 M_n；任意停看时刻由Ville不等式"
    " P(存在n使M_n≥1/α)≤α。启动时锁定α=0.05，即门槛M=20；"
    "该结论对任意多次查看和任意停时均成立，无需α消耗。"
)


def sign_of(difference):
    """Return +1, -1, or 0 for a paired difference."""
    if difference > 0:
        return 1
    if difference < 0:
        return -1
    return 0


def log_increment(nonzero, positive, sign):
    """Log evidence added by one sign, given prior non-zero/positive counts.

    ``nonzero`` and ``positive`` describe the evidence *before* this sample.
    """
    if sign == 0:
        return 0.0
    if sign not in (-1, 1):
        raise ValueError("差值符号必须为 -1、0 或 1")
    if nonzero < 0 or positive < 0 or positive > nonzero:
        raise ValueError("累计符号计数不合法")
    if sign > 0:
        probability = (positive + 0.5) / (nonzero + 1.0)
    else:
        probability = (nonzero - positive + 0.5) / (nonzero + 1.0)
    return math.log(2.0 * probability)


def advance(state, difference):
    """Advance a small state dict by one paired difference.

    State keys: log_e, nonzero, positive, ties, seq.  The returned dict is a
    new object, so callers can keep the previous cumulative evidence.
    """
    sign = sign_of(difference)
    increment = log_increment(state["nonzero"], state["positive"], sign)
    result = {
        "seq": state["seq"] + 1,
        "log_e": state["log_e"] + increment,
        "nonzero": state["nonzero"] + (1 if sign != 0 else 0),
        "positive": state["positive"] + (1 if sign > 0 else 0),
        "ties": state["ties"] + (1 if sign == 0 else 0),
        "sign": sign,
        "log_increment": increment,
    }
    return result


def initial_state():
    return {"seq": 0, "log_e": 0.0, "nonzero": 0, "positive": 0, "ties": 0}


def replay(differences):
    """Return one cumulative state per difference, in inclusion order."""
    state = initial_state()
    steps = []
    for difference in differences:
        state = advance(state, difference)
        steps.append(dict(state))
    return steps


def closed_form_log_e(positive, negative):
    """log M for a completed sign sequence with the given counts."""
    nonzero = positive + negative
    if nonzero == 0:
        return 0.0
    log_beta_ratio = (
        lgamma(positive + 0.5)
        + lgamma(negative + 0.5)
        - lgamma(nonzero + 1.0)
        - (2.0 * lgamma(0.5) - lgamma(1.0))
    )
    return nonzero * math.log(2.0) + log_beta_ratio


def crossed(log_e):
    """Whether the locked two-sided 5% threshold has been reached."""
    return log_e >= LOG_THRESHOLD - 1e-12


def evidence_view(log_e):
    """JSON-safe display values for a log e-value.

    exp(log_e) can overflow for long one-sided sequences, so the UI receives a
    scientific-notation label and log10 as well as the raw log evidence.
    """
    log10 = log_e / math.log(10.0)
    if -700.0 < log_e < 700.0:
        value = math.exp(log_e)
        label = f"{value:.6g}"
    else:
        value = None
        label = f"10^{log10:.4g}"
    return {
        "log_e": float(log_e),
        "log10_e": float(log10),
        "evidence": value,
        "evidence_label": label,
        "threshold": THRESHOLD,
        "crossed": crossed(log_e),
    }
