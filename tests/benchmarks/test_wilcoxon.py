"""Unit tests for the pure-Python Wilcoxon signed-rank implementation.

Reference values are derived from:
  - Hollander & Wolfe (1973) Table A.4 (exact permutation critical values)
  - R's wilcox.test() results documented in the manual and verified manually
  - Hand-enumerated permutation counts for small-N tie cases

No scipy is imported anywhere in this module. These tests verify the
implementation in scripts/ciqs_harness.py independently.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the harness module without importing it as a package (it lives under
# scripts/, not under a proper Python package directory).
# ---------------------------------------------------------------------------

_HARNESS_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ciqs_harness.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("ciqs_harness", _HARNESS_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ciqs_harness"] = mod
    spec.loader.exec_module(mod)
    return mod


_harness = _load_harness()
wilcoxon_signed_rank = _harness.wilcoxon_signed_rank
_average_ranks = _harness._average_ranks
_exact_two_sided_p = _harness._exact_two_sided_p
_normal_two_sided_p = _harness._normal_two_sided_p


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _isclose(a: float, b: float, *, rel_tol: float = 1e-9, abs_tol: float = 1e-12) -> bool:
    return math.isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol)


# ---------------------------------------------------------------------------
# _average_ranks
# ---------------------------------------------------------------------------

class TestAverageRanks:
    def test_no_ties(self):
        ranks = _average_ranks([3.0, 1.0, 2.0])
        assert ranks == [3.0, 1.0, 2.0]

    def test_all_tied(self):
        ranks = _average_ranks([5.0, 5.0, 5.0])
        assert ranks == [2.0, 2.0, 2.0]

    def test_partial_tie(self):
        # values [1, 2, 2, 3] → ranks [1, 2.5, 2.5, 4]
        ranks = _average_ranks([1.0, 2.0, 2.0, 3.0])
        assert ranks == [1.0, 2.5, 2.5, 4.0]

    def test_single(self):
        assert _average_ranks([7.0]) == [1.0]

    def test_two_distinct(self):
        ranks = _average_ranks([0.5, 0.8])
        assert ranks == [1.0, 2.0]


# ---------------------------------------------------------------------------
# wilcoxon_signed_rank — basic contract
# ---------------------------------------------------------------------------

class TestWilcoxonContract:
    def test_returns_three_tuple(self):
        result = wilcoxon_signed_rank([1, 2, 3], [0, 1, 2])
        assert len(result) == 3

    def test_w_is_float(self):
        w, _, _ = wilcoxon_signed_rank([1, 2, 3], [0, 1, 2])
        assert isinstance(w, float)

    def test_n_is_int(self):
        _, n, _ = wilcoxon_signed_rank([1, 2, 3], [0, 1, 2])
        assert isinstance(n, int)

    def test_p_in_unit_interval(self):
        for seed in range(5):
            x = [float(seed + i) for i in range(6)]
            y = [float(i) for i in range(6)]
            _, _, p = wilcoxon_signed_rank(x, y)
            assert 0.0 <= p <= 1.0

    def test_mismatched_length_raises(self):
        with pytest.raises(ValueError, match="equal length"):
            wilcoxon_signed_rank([1, 2, 3], [1, 2])

    def test_bad_method_raises(self):
        with pytest.raises(ValueError, match="method"):
            wilcoxon_signed_rank([1, 2], [0, 0], method="monte_carlo")

    def test_method_exact_accepted(self):
        wilcoxon_signed_rank([1, 2], [0, 0], method="exact")

    def test_method_normal_accepted(self):
        wilcoxon_signed_rank([1, 2], [0, 0], method="normal")


# ---------------------------------------------------------------------------
# All-zero differences
# ---------------------------------------------------------------------------

class TestAllZeroDifferences:
    """When all paired differences are zero, N=0 and p=1.0 by convention."""

    def test_basic(self):
        w, n, p = wilcoxon_signed_rank([1, 2, 3], [1, 2, 3])
        assert w == 0.0
        assert n == 0
        assert p == 1.0

    def test_single_pair_zero(self):
        w, n, p = wilcoxon_signed_rank([4.0], [4.0])
        assert n == 0
        assert p == 1.0

    def test_empty_lists(self):
        # Zero-length input: all differences are (vacuously) zero.
        w, n, p = wilcoxon_signed_rank([], [])
        assert n == 0
        assert p == 1.0


# ---------------------------------------------------------------------------
# Zero-difference exclusion
# ---------------------------------------------------------------------------

class TestZeroDifferenceExclusion:
    """Pairs where x[i]==y[i] must be excluded; N counts only the nonzero ones."""

    def test_mixed_zeros_and_nonzero(self):
        # diffs: [-1, +1, 0, 0, 0] → nonzero: [-1, +1], N=2
        w, n, p = wilcoxon_signed_rank([1, 2, 3, 4, 5], [2, 1, 3, 4, 5], method="exact")
        assert n == 2

    def test_w_with_tie_in_sign(self):
        # Both nonzero diffs have equal |d|=1, so ranks=[1.5, 1.5].
        # W+= 1.5, W-=1.5, W=1.5.  All 4 subsets: sums {0, 1.5, 1.5, 3.0};
        # those ≤ 1.5 are: 0, 1.5, 1.5 → 3 of 4 → p = min(1, 2*3/4) = 1.0
        w, n, p = wilcoxon_signed_rank([1, 2, 3, 4, 5], [2, 1, 3, 4, 5], method="exact")
        assert w == 1.5
        assert n == 2
        assert p == 1.0


# ---------------------------------------------------------------------------
# Hollander & Wolfe (1973) reference — N=9, no ties, exact permutation
# ---------------------------------------------------------------------------

class TestHollanderWolfe:
    """
    Classic reference case: Table A.4 in Hollander & Wolfe "Nonparametric
    Statistical Methods" (1973).  The same numbers appear in R's documentation
    for wilcox.test().

    diffs (x-y): +0.952, -0.147, +1.022, +0.430, +0.620, +0.590, +0.490,
                 -0.080, +0.010
    Sorted |diffs|: 0.010, 0.080, 0.147, 0.430, 0.490, 0.590, 0.620, 0.952,
                    1.022 → ranks 1..9 (no ties)
    Negative signs at ranks 3 (0.147) and 2 (0.080): W- = 3+2 = 5
    W = min(W+, W-) = min(40, 5) = 5
    Exact two-sided p (N=9): 2 * 10 / 512 = 0.0390625
      (10 subsets of {1..9} with sum ≤ 5: {}, {1}, {2}, {3}, {4}, {5},
       {1,2}, {1,3}, {1,4}, {2,3})
    """

    X = [1.83, 0.50, 1.62, 2.48, 1.68, 1.88, 1.55, 3.06, 1.30]
    Y = [0.878, 0.647, 0.598, 2.05, 1.06, 1.29, 1.06, 3.14, 1.29]

    def test_statistic(self):
        w, n, p = wilcoxon_signed_rank(self.X, self.Y, method="exact")
        assert w == 5.0, f"expected W=5.0, got {w}"

    def test_n(self):
        _, n, _ = wilcoxon_signed_rank(self.X, self.Y, method="exact")
        assert n == 9

    def test_p_exact(self):
        _, _, p = wilcoxon_signed_rank(self.X, self.Y, method="exact")
        assert _isclose(p, 0.0390625), f"expected p=0.0390625, got {p}"

    def test_auto_uses_exact_for_n9(self):
        # N=9 ≤ 25, so "auto" should resolve to "exact".
        w1, n1, p1 = wilcoxon_signed_rank(self.X, self.Y, method="auto")
        w2, n2, p2 = wilcoxon_signed_rank(self.X, self.Y, method="exact")
        assert (w1, n1, p1) == (w2, n2, p2)


# ---------------------------------------------------------------------------
# N=1 edge case
# ---------------------------------------------------------------------------

class TestNEquals1:
    """Single nonzero pair: W=0, N=1.  p should equal 1.0 (min=0 is W+ or W-)."""

    def test_positive_diff(self):
        w, n, p = wilcoxon_signed_rank([5.0], [3.0], method="exact")
        assert n == 1
        assert w == 0.0
        # Only two subsets: {} (sum=0) and {1} (sum=1).  W=0: count(sum≤0)=1.
        # p = min(1, 2*1/2) = 1.0
        assert p == 1.0

    def test_negative_diff(self):
        w, n, p = wilcoxon_signed_rank([3.0], [5.0], method="exact")
        assert n == 1
        assert w == 0.0
        assert p == 1.0


# ---------------------------------------------------------------------------
# All-positive differences (W=0, significant for large N)
# ---------------------------------------------------------------------------

class TestAllPositiveDifferences:
    """All diffs positive → W- = 0, so W = min(W+, 0) = 0.

    N=5, distinct |diffs|: {1,4,8,13,19} → ranks 1..5, W+=15, W-=0, W=0.
    Exact: only {} has sum ≤ 0 → p = min(1, 2*1/32) = 0.0625.
    """

    X = [2, 5, 9, 14, 20]
    Y = [1, 1, 1, 1, 1]

    def test_statistic(self):
        w, n, p = wilcoxon_signed_rank(self.X, self.Y, method="exact")
        assert w == 0.0

    def test_n(self):
        _, n, _ = wilcoxon_signed_rank(self.X, self.Y, method="exact")
        assert n == 5

    def test_p(self):
        _, _, p = wilcoxon_signed_rank(self.X, self.Y, method="exact")
        assert _isclose(p, 0.0625), f"expected p=0.0625, got {p}"


# ---------------------------------------------------------------------------
# Tied ranks (rank ties, not zero-difference ties)
# ---------------------------------------------------------------------------

class TestRankTies:
    """All |diffs| are equal → all ranks are the same average → W+==W-.

    x=[3,3,3,3,3,3], y=[1,1,1,5,5,5]: diffs=[+2,+2,+2,-2,-2,-2], N=6.
    All |diffs|=2, ranks=[3.5]*6.  W+=3*3.5=10.5, W-=3*3.5=10.5, W=10.5.
    """

    X = [3, 3, 3, 3, 3, 3]
    Y = [1, 1, 1, 5, 5, 5]

    def test_statistic(self):
        w, n, p = wilcoxon_signed_rank(self.X, self.Y, method="exact")
        assert w == 10.5

    def test_n(self):
        _, n, _ = wilcoxon_signed_rank(self.X, self.Y, method="exact")
        assert n == 6

    def test_p_equals_one(self):
        # W = W+, so P(W+ ≤ W) ≥ 0.5 → two-sided p ≥ 1 → capped at 1.0
        _, _, p = wilcoxon_signed_rank(self.X, self.Y, method="exact")
        assert p == 1.0


# ---------------------------------------------------------------------------
# Normal approximation for large N (N > 25 → auto picks normal)
# ---------------------------------------------------------------------------

class TestNormalApproximation:
    """For N=26 the implementation must switch to the normal approximation.

    Expected values computed by running the harness itself (no scipy).
    The approximation is well-characterised: z-score should be ~4.0 for
    the given data, placing p well below 0.001.
    """

    # 26 pairs, all or almost all with x > y
    X = [
        1.83, 0.50, 1.62, 2.48, 1.68, 1.88, 1.55, 3.06, 1.30,
        2.1, 1.4, 0.9, 3.2, 2.7, 1.1, 0.6, 4.1, 2.3, 1.9, 0.8,
        3.5, 2.2, 1.7, 0.7, 1.3, 2.8,
    ]
    Y = [
        0.878, 0.647, 0.598, 2.05, 1.06, 1.29, 1.06, 3.14, 1.29,
        1.5, 0.8, 1.2, 2.5, 1.9, 0.9, 0.5, 3.2, 1.7, 1.2, 0.6,
        2.8, 1.5, 1.1, 0.3, 0.9, 2.1,
    ]

    def test_auto_uses_normal_for_n26(self):
        # N should be 26 (no zero diffs in this data)
        _, n, _ = wilcoxon_signed_rank(self.X, self.Y, method="auto")
        assert n == 26

    def test_explicit_normal_matches_auto(self):
        r_auto = wilcoxon_signed_rank(self.X, self.Y, method="auto")
        r_norm = wilcoxon_signed_rank(self.X, self.Y, method="normal")
        assert r_auto == r_norm

    def test_p_significant(self):
        _, _, p = wilcoxon_signed_rank(self.X, self.Y, method="normal")
        # This is a large, clear signal; p should be very small.
        assert p < 0.001, f"expected p < 0.001, got {p}"

    def test_p_reference_value(self):
        # Reference value computed from the harness implementation itself.
        # W=13.0, checked against N=26 normal approximation.
        w, n, p = wilcoxon_signed_rank(self.X, self.Y, method="normal")
        assert w == 13.0
        assert _isclose(p, 3.855610715620357e-05, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# Symmetry: wilcoxon_signed_rank(x, y) == wilcoxon_signed_rank(y, x)
# (W and N should be the same; p is two-sided so symmetric)
# ---------------------------------------------------------------------------

class TestSymmetry:
    def test_symmetric(self):
        x = [1.83, 0.50, 1.62, 2.48, 1.68, 1.88, 1.55, 3.06, 1.30]
        y = [0.878, 0.647, 0.598, 2.05, 1.06, 1.29, 1.06, 3.14, 1.29]
        w1, n1, p1 = wilcoxon_signed_rank(x, y, method="exact")
        w2, n2, p2 = wilcoxon_signed_rank(y, x, method="exact")
        assert w1 == w2
        assert n1 == n2
        assert _isclose(p1, p2)


# ---------------------------------------------------------------------------
# No scipy import anywhere in the harness source
# ---------------------------------------------------------------------------

class TestNoDependencyOnScipy:
    def test_no_scipy_in_harness_source(self):
        source = _HARNESS_PATH.read_text(encoding="utf-8")
        assert "import scipy" not in source, (
            "scipy import found in ciqs_harness.py — must remain scipy-free"
        )
        assert "from scipy" not in source, (
            "scipy import found in ciqs_harness.py — must remain scipy-free"
        )
