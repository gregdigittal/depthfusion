"""Budget-aware model selection for the PM dispatch cycle — S-211 (T-722..T-724).

This module closes the budget feedback loop for ``/digittal-method`` runs:

  1. ``Budget`` tracks a spend cap and accumulated spend, exposing
     ``remaining()`` (AC-5, T-723).
  2. ``select_model_for_task`` calls the DepthFusion recommender with the
     per-task ``budget_usd`` and Fable-5 ``exclude_vendors`` filter, returning
     the chosen model plus a ``budget_warning`` flag (T-720 integration).
  3. ``budget_alert`` checks, *before* each dispatch, whether the remaining
     budget can afford the cheapest eligible model (T-723, AC-5). When it
     cannot, the PM surfaces an alert instead of dispatching into an OOM.
  4. ``log_dispatch_outcome`` records the *actual* verdict and cost after the
     agent completes via ``record_model_telemetry`` — closing the feedback loop
     (T-722, AC-4) — and debits the real cost from the budget.
  5. ``build_budget_summary`` produces the human-readable spend-vs-baseline
     report served by ``GET /api/budget-summary`` (T-724, AC-6).

The module has no hard dependency on the MCP transport: ``record_model_telemetry``
is the same callable used by the MCP tool, so the feedback loop works whether
the PM is local or remote.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from depthfusion.analytics.model_stats import get_model_stats
from depthfusion.analytics.recommender import recommend, vendor_for_model

# The default ("sonnet baseline") used by build_budget_summary to compute how
# much each non-baseline choice saved or cost relative to always picking
# Sonnet. Matches the fable5 tier table default dev model.
DEFAULT_BASELINE_MODEL = "claude-sonnet-4"


# ---------------------------------------------------------------------------
# Budget tracking
# ---------------------------------------------------------------------------

@dataclass
class Budget:
    """Tracks a spend cap and accumulated spend across a dispatch run.

    Parameters
    ----------
    cap_usd:
        The total spend cap for the run (e.g. ``$5`` for a 5-task run).
    spent_usd:
        Spend accumulated so far (debited via :meth:`debit`).
    """

    cap_usd: float
    spent_usd: float = 0.0
    _entries: list[dict[str, Any]] = field(default_factory=list)

    def remaining(self) -> float:
        """USD remaining under the cap (never negative for display)."""
        return self.cap_usd - self.spent_usd

    def debit(self, cost_usd: float, *, model_id: str = "", task: str = "") -> float:
        """Debit *cost_usd* from the budget and return the new remaining.

        Negative costs are clamped to 0 to avoid crediting the budget on a
        malformed outcome record.
        """
        cost = max(0.0, float(cost_usd))
        self.spent_usd += cost
        self._entries.append({"model_id": model_id, "task": task, "cost_usd": cost})
        return self.remaining()

    @property
    def entries(self) -> list[dict[str, Any]]:
        """Per-dispatch spend records (model_id, task, cost_usd)."""
        return list(self._entries)


# ---------------------------------------------------------------------------
# Eligible-model cost helpers (T-723)
# ---------------------------------------------------------------------------

def _eligible_stats(
    *,
    exclude_vendors: Optional[list[str]] = None,
    available_models: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Return one stat row per candidate model after vendor exclusion.

    Prefers the ``observed`` row, falls back to ``prior``. Excludes any model
    whose vendor is in *exclude_vendors* (Fable-5 isolation).
    """
    excluded = {v.lower() for v in (exclude_vendors or [])}
    all_stats = get_model_stats()

    if available_models:
        candidates = list(dict.fromkeys(available_models))
    else:
        candidates = sorted({s["model_id"] for s in all_stats})

    chosen: dict[str, dict[str, Any]] = {}
    for model_id in candidates:
        if vendor_for_model(model_id) in excluded:
            continue
        rows = [s for s in all_stats if s["model_id"] == model_id]
        observed = [r for r in rows if r["source"] == "observed"]
        prior = [r for r in rows if r["source"] == "prior"]
        pick = observed[0] if observed else (prior[0] if prior else None)
        if pick is not None:
            chosen[model_id] = pick
    return list(chosen.values())


def min_eligible_cost(
    *,
    exclude_vendors: Optional[list[str]] = None,
    available_models: Optional[list[str]] = None,
) -> Optional[float]:
    """Cheapest ``avg_cost_usd`` among eligible models, or ``None`` if none."""
    stats = _eligible_stats(
        exclude_vendors=exclude_vendors, available_models=available_models
    )
    costs = [float(s["avg_cost_usd"]) for s in stats if s.get("avg_cost_usd") is not None]
    return min(costs) if costs else None


def budget_alert(
    budget: Budget,
    *,
    exclude_vendors: Optional[list[str]] = None,
    available_models: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Pre-dispatch budget check (T-723, AC-5).

    Returns ``{"ok": bool, "remaining": float, "min_cost": float|None, "message": str}``.
    ``ok`` is ``False`` when the remaining budget cannot afford the cheapest
    eligible model — the PM must surface the alert and NOT dispatch.
    """
    remaining = budget.remaining()
    min_cost = min_eligible_cost(
        exclude_vendors=exclude_vendors, available_models=available_models
    )
    if min_cost is None:
        return {
            "ok": False,
            "remaining": remaining,
            "min_cost": None,
            "message": "No eligible models available after vendor exclusion.",
        }
    ok = remaining >= min_cost
    if ok:
        message = (
            f"Budget OK: ${remaining:.4f} remaining covers cheapest eligible "
            f"model at ${min_cost:.4f}."
        )
    else:
        message = (
            f"Budget alert: ${remaining:.4f} remaining is below the cheapest "
            f"eligible model (${min_cost:.4f}). Halt dispatch and surface to user."
        )
    return {"ok": ok, "remaining": remaining, "min_cost": min_cost, "message": message}


# ---------------------------------------------------------------------------
# Model selection (wraps the recommender with budget) — feeds the PM cycle
# ---------------------------------------------------------------------------

def select_model_for_task(
    *,
    task_category: str,
    budget: Budget,
    remaining_tasks: int,
    exclude_vendors: Optional[list[str]] = None,
    available_models: Optional[list[str]] = None,
    context: str = "",
) -> Optional[dict[str, Any]]:
    """Pick the best model for the next task within the per-task budget.

    The per-task ``budget_usd`` is ``budget.remaining / remaining_tasks`` (AC-2),
    floored at the full remaining budget for the final task. Returns the
    top-ranked recommendation dict (including ``budget_warning``) or ``None``
    when no model can be recommended.
    """
    remaining_tasks = max(1, int(remaining_tasks))
    per_task_budget = budget.remaining() / remaining_tasks
    recs = recommend(
        task_category=task_category,
        context=context,
        exclude_vendors=exclude_vendors,
        available_models=available_models,
        budget_usd=per_task_budget,
    )
    if not recs:
        return None
    return recs[0]


# ---------------------------------------------------------------------------
# Outcome logging (T-722, AC-4) — closes the feedback loop
# ---------------------------------------------------------------------------

def log_dispatch_outcome(
    *,
    session_id: str,
    model_id: str,
    task_category: str,
    quality_verdict: str,
    cost_usd: float,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 0,
    project_slug: Optional[str] = None,
    budget: Optional[Budget] = None,
    recorder: Optional[Callable[[dict], dict]] = None,
) -> dict[str, Any]:
    """Record the actual dispatch outcome to DepthFusion telemetry (AC-4).

    Calls ``record_model_telemetry`` (the same callable the MCP tool uses) with
    the real verdict and cost, debits the cost from *budget* when supplied, and
    returns the recorder result. ``recorder`` is injectable for testing.

    All external calls are wrapped so a telemetry failure surfaces as an error
    dict rather than crashing the PM dispatch cycle.
    """
    if recorder is None:
        from depthfusion.mcp.tools.telemetry_tools import record_model_telemetry as recorder

    event = {
        "session_id": session_id,
        "model_id": model_id,
        "task_category": task_category,
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "latency_ms": int(latency_ms),
        "cost_usd": float(cost_usd),
        "quality_verdict": quality_verdict,
    }
    if project_slug is not None:
        event["project_slug"] = project_slug

    try:
        result = recorder(event)
    except Exception as exc:  # pragma: no cover - defensive
        result = {"error": f"telemetry record failed: {exc}"}

    if budget is not None and not (isinstance(result, dict) and result.get("error")):
        budget.debit(cost_usd, model_id=model_id, task=task_category)

    return result


# ---------------------------------------------------------------------------
# Budget summary (T-724, AC-6)
# ---------------------------------------------------------------------------

def _baseline_cost(baseline_model: str) -> float:
    """avg_cost_usd for the baseline model (observed, else prior)."""
    stats = get_model_stats(model_id=baseline_model)
    observed = [s for s in stats if s["source"] == "observed"]
    prior = [s for s in stats if s["source"] == "prior"]
    pick = observed[0] if observed else (prior[0] if prior else None)
    if pick is None:
        return 0.0
    return float(pick.get("avg_cost_usd") or 0.0)


def build_budget_summary(
    *,
    cap_usd: Optional[float] = None,
    project_slug: Optional[str] = None,
    session_id: Optional[str] = None,
    baseline_model: str = DEFAULT_BASELINE_MODEL,
    telemetry_rows: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Human-readable spend vs. recommendations summary (AC-6, T-724).

    Reads actual spend from telemetry rows (filtered by project/session) and
    compares each model choice against the Sonnet baseline cost. Returns
    actual spend, remaining (when ``cap_usd`` given), and per-model savings.

    ``telemetry_rows`` is injectable for testing; when ``None`` the live
    ``model_telemetry`` table is queried.
    """
    if telemetry_rows is None:
        telemetry_rows = _query_telemetry_rows(
            project_slug=project_slug, session_id=session_id
        )

    baseline = _baseline_cost(baseline_model)

    actual_spend = 0.0
    per_model: dict[str, dict[str, Any]] = {}
    for row in telemetry_rows:
        cost = float(row.get("cost_usd") or 0.0)
        actual_spend += cost
        mid = row.get("model_id", "unknown")
        bucket = per_model.setdefault(
            mid,
            {"model_id": mid, "dispatches": 0, "spend_usd": 0.0, "saved_vs_baseline_usd": 0.0},
        )
        bucket["dispatches"] += 1
        bucket["spend_usd"] += cost
        # Positive = cheaper than baseline (saved); negative = pricier (cost more).
        bucket["saved_vs_baseline_usd"] += baseline - cost

    summary: dict[str, Any] = {
        "project_slug": project_slug,
        "session_id": session_id,
        "baseline_model": baseline_model,
        "baseline_cost_usd": baseline,
        "actual_spend_usd": round(actual_spend, 6),
        "dispatch_count": len(telemetry_rows),
        "by_model": [
            {
                "model_id": b["model_id"],
                "dispatches": b["dispatches"],
                "spend_usd": round(b["spend_usd"], 6),
                "saved_vs_baseline_usd": round(b["saved_vs_baseline_usd"], 6),
            }
            for b in sorted(per_model.values(), key=lambda x: -x["spend_usd"])
        ],
    }
    if cap_usd is not None:
        summary["cap_usd"] = float(cap_usd)
        summary["remaining_usd"] = round(float(cap_usd) - actual_spend, 6)
    return summary


def _query_telemetry_rows(
    *,
    project_slug: Optional[str] = None,
    session_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Query model_telemetry rows filtered by project_slug and/or session_id."""
    from contextlib import closing

    from depthfusion.telemetry import schema

    schema.migrate()
    where: list[str] = []
    params: list[Any] = []
    if project_slug is not None:
        where.append("project_slug = ?")
        params.append(project_slug)
    if session_id is not None:
        where.append("session_id = ?")
        params.append(session_id)

    query = "SELECT * FROM model_telemetry"
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY recorded_at ASC"

    try:
        with closing(schema.connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:  # pragma: no cover - defensive
        return []
