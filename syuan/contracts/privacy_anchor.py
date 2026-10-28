"""AggregateAnchor —— 隐私聚合结果的链上锚定。

链上只记录"谁参与、用什么参数、结果哈希是多少"，聚合值本身链下发布。
这样既保留了可审计性（参与方不可抵赖、参数不可事后修改），
又不把任何个体明文或聚合明文写进账本。
"""

from __future__ import annotations

from typing import Any

from .. import config as C
from .runtime import Contract, Revert


class AggregateAnchor(Contract):
    name = "AggregateAnchor"
    addr = C.ADDR_ANCHOR

    def m_anchor(
        self,
        round_id: str,
        result_hash: str,
        party_count: int,
        epsilon: float = 0.0,
        scheme: str = "Paillier-1024 + Laplace",
        params: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        self.require(bool(round_id), "E_ROUND", "轮次编号不能为空")
        self.require(len(result_hash) == 64, "E_HASH", "结果哈希必须是 sha256")
        self.require(self.state.get(self.key("round", round_id)) is None, "E_DUP", "该轮次已锚定")
        self.require(int(party_count) >= 2, "E_PARTIES", "参与方至少 2 家")

        rec = {
            "round_id": round_id,
            "result_hash": result_hash,
            "party_count": int(party_count),
            "epsilon": float(epsilon),
            "scheme": scheme,
            "params": params or {},
            "note": note,
            "anchored_by": self.ctx.sender,
            "anchored_by_name": self.ctx.engine.names.get(self.ctx.sender, "未登记主体"),
            "ts": self.now,
        }
        self.state.set(self.key("round", round_id), rec)
        self.emit(
            "AggregateAnchored",
            {
                "round_id": round_id,
                "party_count": int(party_count),
                "epsilon": float(epsilon),
                "scheme": scheme,
                "result_hash": result_hash,
            },
        )
        return rec

    def m_rounds(self, limit: int = 20) -> list[dict[str, Any]]:
        items = sorted(self.state.scan(self.key("round", "")), key=lambda r: r["ts"], reverse=True)
        return self.paginate(items, limit)

    def m_get(self, round_id: str) -> dict[str, Any]:
        rec = self.state.get(self.key("round", round_id))
        if rec is None:
            raise Revert("E_NOT_FOUND", f"轮次不存在：{round_id}")
        return rec
