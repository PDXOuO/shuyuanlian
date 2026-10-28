"""Settlement —— 沿血缘 DAG 的自动分账。

核心约束：**整数守恒**。全部金额以微元为单位做整数运算，
每次分配用"最大余数法"处理不可整除的尾差，保证
    sum(各方所得) == 本次收入
在 1000 次连续调用后误差恒为 0（测试报告第 5 节有实测数据）。
"""

from __future__ import annotations

from typing import Any

from .. import config as C
from .runtime import Contract, Revert


def allocate(amount: int, items: list[tuple[str, int]]) -> dict[str, int]:
    """按权重把整数 amount 精确拆分为若干份，总和严格等于 amount。

    items: [(key, weight)]，weight 为正整数。
    """
    if amount < 0:
        raise ValueError("amount must be non-negative")
    total_w = sum(w for _, w in items)
    if total_w <= 0:
        raise ValueError("weights must be positive")
    base = {k: (amount * w) // total_w for k, w in items}
    rem = amount - sum(base.values())
    if rem:
        frac = {k: (amount * w) % total_w for k, w in items}
        order = sorted(items, key=lambda kv: (-frac[kv[0]], kv[0]))
        for i in range(rem):
            base[order[i % len(order)][0]] += 1
    assert sum(base.values()) == amount, "分账必须守恒"
    return base


class Settlement(Contract):
    name = "Settlement"
    addr = C.ADDR_SETTLEMENT

    # ------------------------------------------------------------------ 内部
    def _credit(self, addr: str, element_id: str, amount: int) -> None:
        bk = self.key("balance", addr)
        self.state.set(bk, int(self.state.get(bk, 0)) + amount)
        ek = self.key("earned", f"{addr}:{element_id}")
        self.state.set(ek, int(self.state.get(ek, 0)) + amount)

    def _distribute(self, element_id: str, amount: int, parts: list[dict[str, Any]], hop: int) -> None:
        if amount <= 0:
            return
        el = self.call(C.ADDR_REGISTRY, "getElement", {"element_id": element_id})
        parents = el["parents"]
        if not parents:
            self._credit(el["owner"], element_id, amount)
            parts.append(
                {
                    "element_id": element_id,
                    "element_name": el["name"],
                    "address": el["owner"],
                    "name": el["owner_name"],
                    "amount_micro": amount,
                    "role": "原始数据持有方" if hop == 0 else "上游数据持有方",
                    "hop": hop,
                }
            )
            return

        self_share = amount * int(el["self_share_bp"]) // C.BP
        rest = amount - self_share
        if self_share:
            self._credit(el["owner"], element_id, self_share)
            parts.append(
                {
                    "element_id": element_id,
                    "element_name": el["name"],
                    "address": el["owner"],
                    "name": el["owner_name"],
                    "amount_micro": self_share,
                    "role": "加工方（当前元件持有者）" if hop == 0 else f"上游加工方（第 {hop} 跳）",
                    "hop": hop,
                }
            )
        if rest:
            alloc = allocate(rest, [(p["id"], int(p["weight"])) for p in parents])
            for pid, amt in alloc.items():
                self._distribute(pid, amt, parts, hop + 1)

    # ------------------------------------------------------------------ 结算
    def m_settle(
        self,
        element_id: str,
        amount_micro: int,
        policy_id: str = "",
        payer: str = "",
    ) -> dict[str, Any]:
        internal = self.ctx.caller == C.ADDR_ACCESS
        roles = self.ctx.engine.roles.get(self.ctx.sender, [])
        self.require(internal or C.ROLE_ADMIN in roles, "E_AUTH", "仅授权合约或治理方可触发结算")
        amount = int(amount_micro)
        self.require(amount > 0, "E_AMOUNT", "结算金额必须为正")

        parts: list[dict[str, Any]] = []
        self._distribute(element_id, amount, parts, 0)
        dist_sum = sum(p["amount_micro"] for p in parts)
        if dist_sum != amount:  # 理论上不可达，作为最后一道防线
            raise Revert("E_CONSERVATION", f"分账不守恒：{dist_sum} != {amount}")

        cnt = int(self.state.get(self.key("count"), 0)) + 1
        self.state.set(self.key("count"), cnt)
        vol = int(self.state.get(self.key("volume"), 0)) + amount
        self.state.set(self.key("volume"), vol)
        ek = self.key("element_total", element_id)
        self.state.set(ek, int(self.state.get(ek, 0)) + amount)

        self.emit(
            "Settled",
            {
                "policy_id": policy_id,
                "element_id": element_id,
                "payer": payer,
                "payer_name": self.ctx.engine.names.get(payer, payer),
                "amount_micro": amount,
                "parts": parts,
                "seq": cnt,
            },
        )
        return {"amount_micro": amount, "parts": parts, "seq": cnt}

    # ------------------------------------------------------------------ 查询
    def m_balanceOf(self, addr: str) -> int:
        return int(self.state.get(self.key("balance", addr), 0))

    def m_balances(self) -> list[dict[str, Any]]:
        out = []
        for k in self.state.keys(self.key("balance", "")):
            addr = k.rsplit(":", 1)[-1]
            out.append(
                {
                    "address": addr,
                    "name": self.ctx.engine.names.get(addr, "未登记主体"),
                    "balance_micro": int(self.state.data[k]),
                }
            )
        out.sort(key=lambda r: r["balance_micro"], reverse=True)
        return out

    def m_earnings(self, addr: str) -> list[dict[str, Any]]:
        out = []
        for k in self.state.keys(self.key("earned", f"{addr}:")):
            element_id = k.rsplit(":", 1)[-1]
            out.append({"element_id": element_id, "amount_micro": int(self.state.data[k])})
        out.sort(key=lambda r: r["amount_micro"], reverse=True)
        return out

    def m_stats(self) -> dict[str, Any]:
        return {
            "settlements": int(self.state.get(self.key("count"), 0)),
            "volume_micro": int(self.state.get(self.key("volume"), 0)),
        }
