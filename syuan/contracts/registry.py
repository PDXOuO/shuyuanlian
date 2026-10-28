"""DataElementRegistry —— 数据元件确权与血缘登记。

链上只保存"承诺"（内容哈希、字段模式哈希、血缘父节点、计费规则），
原始数据始终留在持有方本地，这是"数据可用不可见"在链层的落点。
"""

from __future__ import annotations

from typing import Any

from .. import config as C
from ..crypto_utils import sha256_hex
from .runtime import Contract, Revert

MAX_DEPTH = 8


class DataElementRegistry(Contract):
    name = "DataElementRegistry"
    addr = C.ADDR_REGISTRY

    # ------------------------------------------------------------------ 参与者
    def m_registerParticipant(self, address: str, name: str, roles: list[str]) -> dict[str, Any]:
        self.ctx.require_role(C.ROLE_ADMIN)
        self.require(bool(address) and address.startswith("0x"), "E_ADDR", "非法地址")
        self.require(bool(roles), "E_ROLE", "至少需要一个角色")
        rec = {"address": address, "name": name, "roles": list(roles), "joined_at": self.now}
        self.state.set(self.key("participant", address), rec)
        self.ctx.engine.roles[address] = list(roles)
        self.ctx.engine.names[address] = name
        self.emit("ParticipantRegistered", {"address": address, "name": name, "roles": roles})
        return rec

    def m_participant(self, address: str) -> dict[str, Any] | None:
        return self.state.get(self.key("participant", address))

    def m_participants(self) -> list[dict[str, Any]]:
        return sorted(self.state.scan(self.key("participant", "")), key=lambda r: r["joined_at"])

    # ------------------------------------------------------------------ 元件登记
    def m_registerElement(
        self,
        element_id: str,
        name: str,
        content_hash: str,
        schema_hash: str,
        parents: list[dict[str, Any]] | None = None,
        price_micro: int = 0,
        self_share_bp: int = C.DEFAULT_SELF_SHARE_BP,
        category: str = "原始数据",
        rows: int = 0,
        quantum: float = C.FINGERPRINT_QUANTUM,
        note: str = "",
    ) -> dict[str, Any]:
        parents = parents or []
        self.require(bool(element_id) and len(element_id) >= 4, "E_ID", "元件 ID 非法")
        self.require(self.state.get(self.key("element", element_id)) is None, "E_DUP", "元件已存在")
        self.require(len(content_hash) == 64, "E_HASH", "内容哈希必须是 sha256")
        self.require(0 <= self_share_bp <= C.BP, "E_SHARE", "分成比例越界")
        self.require(int(price_micro) >= 0, "E_PRICE", "计价不能为负")

        depth = 0
        for p in parents:
            pid = p.get("id")
            parent = self.state.get(self.key("element", pid))
            self.require(parent is not None, "E_PARENT", f"父元件不存在：{pid}")
            self.require(parent["status"] != "FROZEN", "E_PARENT_FROZEN", f"父元件已被冻结：{pid}")
            self.require(int(p.get("weight", 1)) > 0, "E_WEIGHT", "父节点权重必须为正")
            self.require(parent["depth"] + 1 <= MAX_DEPTH, "E_DEPTH", "血缘深度超限")
            depth = max(depth, parent["depth"] + 1)

        owner = self.ctx.sender
        rec = {
            "id": element_id,
            "name": name,
            "owner": owner,
            "owner_name": self.ctx.engine.names.get(owner, "未登记主体"),
            "content_hash": content_hash,
            "schema_hash": schema_hash,
            "parents": [{"id": p["id"], "weight": int(p.get("weight", 1))} for p in parents],
            "price_micro": int(price_micro),
            "self_share_bp": int(self_share_bp),
            "category": category,
            "rows": int(rows),
            "quantum": float(quantum),
            "note": note,
            "depth": depth,
            "status": "ACTIVE",
            "created_at": self.now,
        }
        self.state.set(self.key("element", element_id), rec)
        self.emit(
            "ElementRegistered",
            {
                "element_id": element_id,
                "name": name,
                "owner": owner,
                "parents": [p["id"] for p in rec["parents"]],
                "content_hash": content_hash,
                "depth": depth,
            },
        )
        return rec

    # ------------------------------------------------------------------ 查询
    def m_getElement(self, element_id: str) -> dict[str, Any]:
        rec = self.state.get(self.key("element", element_id))
        if rec is None:
            raise Revert("E_NOT_FOUND", f"元件不存在：{element_id}")
        return rec

    def m_listElements(self, limit: int = 100) -> list[dict[str, Any]]:
        items = sorted(self.state.scan(self.key("element", "")), key=lambda r: r["created_at"])
        return self.paginate(items, limit)

    def m_ancestors(self, element_id: str) -> dict[str, Any]:
        """向上追溯血缘（BFS），返回节点与边，供前端绘制 DAG。"""
        seen: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, str]] = []
        frontier = [element_id]
        while frontier:
            cur = frontier.pop()
            if cur in seen:
                continue
            rec = self.state.get(self.key("element", cur))
            if rec is None:
                continue
            seen[cur] = {
                "id": rec["id"],
                "name": rec["name"],
                "owner": rec["owner"],
                "owner_name": rec["owner_name"],
                "depth": rec["depth"],
                "status": rec["status"],
                "price_micro": rec["price_micro"],
            }
            for p in rec["parents"]:
                edges.append({"from": p["id"], "to": cur, "weight": p["weight"]})
                frontier.append(p["id"])
        return {"nodes": list(seen.values()), "edges": edges}

    def m_ownerOf(self, element_id: str) -> str:
        return self.m_getElement(element_id)["owner"]

    # ------------------------------------------------------------------ 管理
    def m_setPrice(self, element_id: str, price_micro: int) -> dict[str, Any]:
        rec = self.m_getElement(element_id)
        self.require(self.ctx.sender in (rec["owner"],) or C.ROLE_ADMIN in self.ctx.engine.roles.get(self.ctx.sender, []),
                     "E_OWNER", "仅元件持有者或治理方可调价")
        self.require(int(price_micro) >= 0, "E_PRICE", "计价不能为负")
        rec["price_micro"] = int(price_micro)
        rec["updated_at"] = self.now
        self.state.set(self.key("element", element_id), rec)
        self.emit("PriceUpdated", {"element_id": element_id, "price_micro": int(price_micro)})
        return rec

    def m_freezeElement(self, element_id: str, reason: str = "") -> dict[str, Any]:
        """冻结由 TraceAndFreeze 合约内部调用，也允许治理方直接冻结。"""
        internal = self.ctx.caller == C.ADDR_TRACE
        roles = self.ctx.engine.roles.get(self.ctx.sender, [])
        self.require(internal or C.ROLE_ADMIN in roles, "E_AUTH", "仅追踪合约或治理方可冻结")
        rec = self.m_getElement(element_id)
        rec["status"] = "FROZEN"
        rec["frozen_at"] = self.now
        rec["frozen_reason"] = reason
        self.state.set(self.key("element", element_id), rec)
        self.emit("ElementFrozen", {"element_id": element_id, "reason": reason})
        return rec

    def m_schemaDigest(self, columns: list[str]) -> str:
        """字段模式哈希：只暴露"有哪些字段"，不暴露任何取值。"""
        return sha256_hex("|".join(sorted(columns)))
