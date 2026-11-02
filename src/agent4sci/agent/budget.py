from __future__ import annotations

from ..llm.types import Usage


class Budget:
    """Hierarchical USD budget (run → loop → node → agent). Charges propagate upwards."""

    def __init__(self, limit_usd: float | None = None, parent: "Budget | None" = None, name: str = "") -> None:
        self.limit_usd = limit_usd
        self.parent = parent
        self.name = name
        self.spent = 0.0
        self.usage = Usage()

    def charge(self, cost: float | None, usage: Usage | None = None) -> None:
        if cost:
            self.spent += cost
        if usage is not None:
            self.usage.add(usage.model_copy(update={"cost_usd": None}))
        if self.parent is not None:
            self.parent.charge(cost, usage)

    def _chain(self) -> list["Budget"]:
        node: Budget | None = self
        chain = []
        while node is not None:
            chain.append(node)
            node = node.parent
        return chain

    @property
    def remaining(self) -> float | None:
        values = [b.limit_usd - b.spent for b in self._chain() if b.limit_usd is not None]
        return min(values) if values else None

    def exceeded(self) -> bool:
        return any(b.limit_usd is not None and b.spent >= b.limit_usd for b in self._chain())

    def nearly_exhausted(self, ratio: float = 0.9) -> bool:
        return any(b.limit_usd is not None and b.spent >= b.limit_usd * ratio for b in self._chain())

    def child(self, limit_usd: float | None = None, name: str = "") -> "Budget":
        return Budget(limit_usd, parent=self, name=name)

    def snapshot(self) -> dict[str, float | None]:
        return {"spent": round(self.spent, 6), "limit": self.limit_usd, "remaining": self.remaining}
