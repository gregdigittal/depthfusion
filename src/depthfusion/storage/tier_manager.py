"""Tier detection and routing for DepthFusion install modes."""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Tier(Enum):
    LOCAL = "local"
    VPS_TIER1 = "vps-tier1"
    VPS_TIER2 = "vps-tier2"


@dataclass
class TierConfig:
    tier: Tier
    corpus_size: int
    threshold: int
    sessions_until_promotion: int
    mode: str


class TierManager:
    """Detect active tier based on corpus size and environment config."""

    def __init__(self):
        self.threshold = int(os.environ.get("DEPTHFUSION_TIER_THRESHOLD", "500"))
        self.mode = os.environ.get("DEPTHFUSION_MODE", "local")
        _default_autopromote = "true" if self.mode == "vps" else "false"
        self.auto_promote = (
            os.environ.get("DEPTHFUSION_TIER_AUTOPROMOTE", _default_autopromote).lower()
            == "true"
        )

    def _count_corpus(self) -> int:
        home = Path.home()
        count = 0
        for path, pattern in [
            (home / ".claude" / "sessions", "*.tmp"),
            (home / ".claude" / "shared" / "discoveries", "*.md"),
        ]:
            if path.exists():
                count += len(list(path.glob(pattern)))
        memory = home / ".claude" / "projects" / "-home-gregmorris" / "memory"
        if memory.exists():
            count += len([f for f in memory.glob("*.md") if f.name != "MEMORY.md"])
        return count

    def detect_tier(self) -> TierConfig:
        corpus = self._count_corpus()
        if self.mode == "local":
            tier = Tier.LOCAL
        elif corpus >= self.threshold:
            tier = Tier.VPS_TIER2
        else:
            tier = Tier.VPS_TIER1
        sessions_until = max(0, self.threshold - corpus) if tier != Tier.LOCAL else 0
        return TierConfig(
            tier=tier,
            corpus_size=corpus,
            threshold=self.threshold,
            sessions_until_promotion=sessions_until,
            mode=self.mode,
        )
