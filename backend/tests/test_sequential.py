"""Statistical checks for the sequential e-process.

These are independent of the web application: they verify the per-sample
update rule, the closed form, the sign-flip invariance and the actual
false-positive rate under the null when looking after every new pair.
"""

import math
import random

from src.services import sequential


def signs_sequence(differences):
    return [sequential.sign_of(value) for value in differences]


def test_locked_threshold_is_twenty():
    assert sequential.ALPHA == 0.05
    assert sequential.THRESHOLD == 20.0
    assert sequential.LOG_THRESHOLD == math.log(20.0)


def test_per_sample_increment_matches_closed_form():
    cases = [
        [1],
        [1, 1],
        [1, -1],
        [-1, -1, -1, -1],
        [1, 1, -1, 1, -1, -1, 1, -1, 1, 1],
    ]
    for differences in cases:
        steps = sequential.replay(differences)
        positive = sum(1 for x in differences if x > 0)
        negative = sum(1 for x in differences if x < 0)
        expected = sequential.closed_form_log_e(positive, negative)
        assert math.isclose(steps[-1]['log_e'], expected, rel_tol=0, abs_tol=1e-10)


def test_first_pair_has_unit_wealth_because_predictive_is_one_half():
    # Beta(1/2,1/2) predicts each sign with probability 1/2 before any data,
    # so the first payoff is 2 * 1/2 = 1.
    steps = sequential.replay([0.4])
    assert math.isclose(steps[0]['log_increment'], 0.0, abs_tol=1e-12)
    assert steps[0]['log_e'] == 0.0


def test_second_positive_pair_adds_log_three_halves():
    steps = sequential.replay([0.4, 0.2])
    # After one positive: p(+) = (1 + 1/2) / (1 + 1) = 3/4.
    assert math.isclose(steps[1]['log_increment'], math.log(1.5), abs_tol=1e-12)
    assert math.isclose(steps[1]['log_e'], math.log(1.5), abs_tol=1e-12)


def test_cumulative_log_evidence_is_monotone_in_steps():
    steps = sequential.replay([0.3, -0.1, 0.5, 0.2, -0.4, 0.7])
    for previous, current in zip(steps, steps[1:]):
        assert current['seq'] == previous['seq'] + 1
        assert math.isclose(current['log_e'], previous['log_e'] + current['log_increment'],
                            abs_tol=1e-12)


def test_zero_difference_is_a_tie_and_adds_no_evidence():
    steps = sequential.replay([0.2, 0.0, 0.3])
    assert steps[1]['sign'] == 0
    assert steps[1]['log_increment'] == 0.0
    assert steps[1]['log_e'] == steps[0]['log_e']
    assert steps[1]['ties'] == 1
    assert steps[2]['seq'] == 3


def test_swapping_before_after_negates_signs_and_keeps_evidence():
    original = [0.4, -0.2, 0.9, 0.1, -0.3]
    swapped = [-x for x in original]
    left = sequential.replay(original)[-1]
    right = sequential.replay(swapped)[-1]
    assert left['nonzero'] == right['nonzero']
    assert left['positive'] + right['positive'] == left['nonzero']
    assert math.isclose(left['log_e'], right['log_e'], abs_tol=1e-12)


def test_crossed_only_at_or_above_twenty():
    assert not sequential.crossed(math.log(19.999))
    assert sequential.crossed(math.log(20.0))
    assert sequential.crossed(sequential.LOG_THRESHOLD)


def test_seven_positive_pairs_cross_threshold():
    steps = sequential.replay([1.0] * 7)
    assert steps[5]['log_e'] < sequential.LOG_THRESHOLD
    assert sequential.crossed(steps[6]['log_e'])


def test_evidence_view_is_json_safe_for_extreme_sequences():
    view = sequential.evidence_view(sequential.closed_form_log_e(2000, 0))
    assert view['evidence'] is None
    assert view['evidence_label'].startswith('10^')
    assert view['crossed'] is True
    # A long run of one sign is evidence of imbalance in either direction;
    # balanced signs shrink the wealth instead.
    small = sequential.evidence_view(sequential.closed_form_log_e(1000, 1000))
    assert small['evidence'] is not None
    assert small['evidence'] < 1.0
    assert small['crossed'] is False


def _simulate_false_positive_rate(trials, max_pairs, seed, shift=0.0):
    rng = random.Random(seed)
    crossings = 0
    for _ in range(trials):
        state = sequential.initial_state()
        for _ in range(max_pairs):
            difference = rng.gauss(shift, 1.0)
            state = sequential.advance(state, difference)
            if sequential.crossed(state['log_e']):
                crossings += 1
                break
    return crossings / trials


def test_false_positive_rate_is_controlled_when_looking_every_pair():
    # Null: independent differences symmetric about zero.  Looking after every
    # single pair must still keep the family-wise error at or below 5%.  The
    # true bound is alpha; Ville's inequality guarantees it for all stopping
    # rules, so sampling error can only push the estimate down or slightly up.
    rate = _simulate_false_positive_rate(trials=4000, max_pairs=120, seed=20261117)
    assert rate <= 0.05 + 0.012


def test_false_positive_rate_below_alpha_with_shift_free_null():
    # A different null seed and a longer horizon; still bounded by 5%.
    rate = _simulate_false_positive_rate(trials=3000, max_pairs=200, seed=7)
    assert rate <= 0.05 + 0.015


def test_power_increases_with_signal():
    null_rate = _simulate_false_positive_rate(trials=1200, max_pairs=200, seed=11, shift=0.0)
    signal_rate = _simulate_false_positive_rate(trials=1200, max_pairs=200, seed=11, shift=0.6)
    assert signal_rate > null_rate + 0.5


def _simulate_discrete_false_positive_rate(trials, max_pairs, seed):
    """Symmetric discrete null with ties: -1, 0, +1 with probabilities 1/4, 1/2, 1/4."""
    rng = random.Random(seed)
    crossings = 0
    for _ in range(trials):
        state = sequential.initial_state()
        for _ in range(max_pairs):
            difference = rng.choices([-1.0, 0.0, 1.0], weights=[1, 2, 1])[0]
            state = sequential.advance(state, difference)
            if sequential.crossed(state['log_e']):
                crossings += 1
                break
    return crossings / trials


def test_ties_keep_false_positive_rate_controlled():
    rate = _simulate_discrete_false_positive_rate(trials=4000, max_pairs=150, seed=99)
    assert rate <= 0.05 + 0.012


def test_any_look_schedule_keeps_false_positive_rate_controlled():
    """Only look every k-th pair, with k varying by trial: optional stopping
    at arbitrary data-dependent times must not inflate the error rate."""
    rng = random.Random(5150)
    trials = 3000
    crossings = 0
    for _ in range(trials):
        state = sequential.initial_state()
        next_look = rng.randint(1, 5)
        for index in range(1, 201):
            state = sequential.advance(state, rng.gauss(0.0, 1.0))
            if index >= next_look:
                if sequential.crossed(state['log_e']):
                    crossings += 1
                    break
                next_look = index + rng.randint(1, 5)
    assert crossings / trials <= 0.05 + 0.012
