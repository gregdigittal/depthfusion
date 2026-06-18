"""Model recommendation engine — T-716 (S-210).

Ranks candidate models by *quality per dollar* and applies the Fable-5 vendor
isolation filter so a reviewer never runs on the same vendor that just did the
dev work.

Ranking signal (AC-4)
---------------------
Primary: ``quality_rate / cost_per_pass`` (maximise quality earned per dollar
spent), where ``cost_per_pass = avg_cost_usd / quality_rate``. Substituting,
the primary score simplifies to ``quality_rate**2 / avg_cost_usd`` — higher is
better. Ties are broken by ``avg_cost_usd`` ascending (prefer the cheaper model
when quality-per-dollar is equal).

Vendor isolation (AC-3)
----------------------
``exclude_vendors`` removes every model whose provider is in the list. This is
the Fable-5 invariant: dev and review for the same task must never use the same
vendor (see ``fable5-pm-orchestration.md``).

Prior blending (AC-5)
--------------------
Models with ``confidence == "low"`` (observed ``sample_count < 10``) are *not*
rejected — their stats already incorporate hard-coded priors via
``model_stats.get_model_stats`` and the low confidence is surfaced in the
output. Recommendations are never cached (AC-6) because they depend on the
per-call ``exclude_vendors`` argument.
"""
from __future__ import annotations

from typing import Any, Optional

from depthfusion.analytics.model_stats import _get_prior, get_model_stats

# ---------------------------------------------------------------------------
# Known provider (vendor) enum — Fable-5 model pool
# ---------------------------------------------------------------------------
#
# These are the vendors recognised by the Fable-5 orchestration pattern. The
# recommend_model tool validates exclude_vendors against this set (T-717).

KNOWN_PROVIDERS: frozenset[str] = frozenset(
    {"anthropic", "openai", "deepseek", "google", "cursor"}
)

# Map model_id (or a substring of it) to its vendor. Checked in order; the
# first substring match wins. Anthropic short names (opus/sonnet/haiku) and the
# canonical claude-* ids both resolve to "anthropic".
_VENDOR_PATTERNS: tuple[tuple[str, str], ...] = (
    ("claude", "anthropic"),
    ("opus", "anthropic"),
    ("sonnet", "anthropic"),
    ("haiku", "anthropic"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("codex", "openai"),
    ("deepseek", "deepseek"),
    ("gemini", "google"),
    ("cursor", "cursor"),
)

# Confidence ordering for the optional min_confidence filter.
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

# Default candidate pool when available_models is not supplied. These are the
# canonical Anthropic models that carry priors; observed models seen in
# telemetry are merged in at recommendation time.
_DEFAULT_CANDIDATES: tuple[str, ...] = (
    "claude-opus-4",
    "claude-sonnet-4",
    "claude-haiku-4",
)


def vendor_for_model(model_id: str) -> str:
    """Return the vendor for *model_id*, or ``"unknown"`` if unrecognised."""
    lower = model_id.lower()
    for pattern, vendor in _VENDOR_PATTERNS:
        if pattern in lower:
            return vendor
    return "unknown"


def _observed_stat_for(stats: list[dict[str, Any]], model_id: str) -> Optional[dict[str, Any]]:
    """Pick the best single stat row for *model_id* from get_model_stats output.

    Prefers the ``observed`` row (which already blends priors at low n); falls
    back to a ``prior`` row when no observed data exists.
    """
    observed = [s for s in stats if s["model_id"] == model_id and s["source"] == "observed"]
    if observed:
        return observed[0]
    prior = [s for s in stats if s["model_id"] == model_id and s["source"] == "prior"]
    if prior:
        return prior[0]
    return None


def _cost_per_pass(quality_rate: float, avg_cost_usd: float) -> float:
    """Cost incurred per successful (PASS) outcome.

    cost_per_pass = avg_cost_usd / quality_rate. Returns ``inf`` when the model
    never passes (quality_rate == 0) so it ranks last rather than crashing.
    """
    if quality_rate <= 0.0:
        return float("inf")
    return avg_cost_usd / quality_rate


def _primary_score(quality_rate: float, cost_per_pass: float) -> float:
    """quality_rate / cost_per_pass — higher is better. 0.0 if no quality."""
    if cost_per_pass == float("inf") or cost_per_pass <= 0.0:
        return 0.0
    return quality_rate / cost_per_pass


def _build_rationale(
    *,
    model_id: str,
    task_category: str,
    quality_rate: float,
    sample_count: int,
    confidence: str,
    source: str,
    rank: int,
) -> str:
    """Generate a one-sentence human-readable rationale."""
    qpct = f"{quality_rate * 100:.0f}%"
    if source == "prior" or sample_count == 0:
        return (
            f"no observed data for {task_category}; using hard-coded prior "
            f"(quality {qpct}, confidence {confidence})"
        )
    lead = "highest" if rank == 1 else f"rank #{rank}"
    return (
        f"{lead} quality-per-dollar for {task_category} with quality_rate "
        f"{qpct} over n={sample_count} samples (confidence {confidence})"
    )


def recommend(
    *,
    task_category: str,
    context: str = "",
    exclude_vendors: Optional[list[str]] = None,
    available_models: Optional[list[str]] = None,
    min_confidence: Optional[str] = None,
    budget_usd: Optional[float] = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Return a ranked list of up to *top_k* model recommendations.

    Parameters
    ----------
    task_category:
        The task category the model will be used for (informational; surfaced
        in the rationale).
    context:
        Brief free-text description of the specific task (informational).
    exclude_vendors:
        Vendors to exclude (Fable-5 isolation). Models from these providers are
        removed regardless of score.
    available_models:
        Restrict candidates to these model ids. Defaults to all known models
        (priors) merged with any models observed in telemetry.
    min_confidence:
        Filter out models whose confidence is below this threshold
        ("low" | "medium" | "high").
    budget_usd:
        Optional per-task spend cap (S-211, AC-1). Only models whose
        ``avg_cost_usd`` is <= ``budget_usd`` are considered. If no model
        qualifies, the single cheapest available model is returned with
        ``budget_warning = True`` so the caller can decide whether to proceed.
        When ``None`` (the default) no budget filtering is applied and
        ``budget_warning`` is always ``False``.
    top_k:
        Maximum number of recommendations to return.

    Returns
    -------
    list[dict]
        Each dict: model_id, provider, rank, quality_rate, avg_cost_usd,
        cost_per_pass, confidence, rationale, source, sample_count,
        budget_warning.
    """
    excluded = {v.lower() for v in (exclude_vendors or [])}

    # Pull all observed/prior stats once (no model_id filter so we can merge).
    all_stats = get_model_stats()

    # Determine the candidate model set.
    if available_models:
        candidates = list(dict.fromkeys(available_models))
    else:
        observed_models = {
            s["model_id"] for s in all_stats if s["source"] == "observed"
        }
        candidates = list(dict.fromkeys((*_DEFAULT_CANDIDATES, *sorted(observed_models))))

    min_rank = _CONFIDENCE_RANK.get((min_confidence or "low").lower(), 0)

    scored: list[dict[str, Any]] = []
    for model_id in candidates:
        provider = vendor_for_model(model_id)

        # Fable-5 vendor isolation (AC-3).
        if provider in excluded:
            continue

        stat = _observed_stat_for(all_stats, model_id)
        if stat is None:
            # No observed/prior row from telemetry; fall back to the prior table.
            prior = _get_prior(model_id)
            if prior is None:
                continue
            stat = {
                "model_id": model_id,
                "quality_rate": prior["quality_rate"],
                "avg_cost_usd": prior["avg_cost_usd"],
                "sample_count": 0,
                "confidence": "low",
                "source": "prior",
            }

        confidence = stat["confidence"]
        if _CONFIDENCE_RANK.get(confidence, 0) < min_rank:
            continue

        quality_rate = float(stat["quality_rate"])
        avg_cost_usd = float(stat["avg_cost_usd"])
        cpp = _cost_per_pass(quality_rate, avg_cost_usd)
        score = _primary_score(quality_rate, cpp)

        scored.append(
            {
                "model_id": model_id,
                "provider": provider,
                "quality_rate": quality_rate,
                "avg_cost_usd": avg_cost_usd,
                "cost_per_pass": cpp,
                "confidence": confidence,
                "source": stat["source"],
                "sample_count": int(stat["sample_count"]),
                "_score": score,
            }
        )

    # Rank: primary score descending, tie-break by avg_cost_usd ascending (AC-4).
    scored.sort(key=lambda r: (-r["_score"], r["avg_cost_usd"]))

    # Budget filter (S-211 T-720, AC-1). Only models whose avg_cost_usd is
    # within budget are considered. If none qualify, fall back to the single
    # cheapest model and flag budget_warning so the caller can decide.
    budget_warning = False
    selected = scored
    if budget_usd is not None and scored:
        within_budget = [r for r in scored if r["avg_cost_usd"] <= budget_usd]
        if within_budget:
            selected = within_budget
        else:
            # No model fits the budget: return the cheapest one, warned.
            cheapest = min(scored, key=lambda r: r["avg_cost_usd"])
            selected = [cheapest]
            budget_warning = True

    results: list[dict[str, Any]] = []
    for idx, row in enumerate(selected[:top_k], start=1):
        rationale = _build_rationale(
            model_id=row["model_id"],
            task_category=task_category,
            quality_rate=row["quality_rate"],
            sample_count=row["sample_count"],
            confidence=row["confidence"],
            source=row["source"],
            rank=idx,
        )
        cpp = row["cost_per_pass"]
        results.append(
            {
                "model_id": row["model_id"],
                "provider": row["provider"],
                "rank": idx,
                "quality_rate": row["quality_rate"],
                "avg_cost_usd": row["avg_cost_usd"],
                "cost_per_pass": None if cpp == float("inf") else cpp,
                "confidence": row["confidence"],
                "rationale": rationale,
                "source": row["source"],
                "sample_count": row["sample_count"],
                "budget_warning": budget_warning,
            }
        )
    return results
