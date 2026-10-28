"""AccessPolicy —— 授权策略合约化：谁、为什么目的、能用几次、到什么时候。

状态机：ACTIVE → EXHAUSTED / EXPIRED / REVOKED / FROZEN
任何非法调用在链上被 revert，返回可审计的错误码。
"""

from __future__ import annotations

from typing import Any

from .. import config as C
from ..crypto_utils import sha256_hex
from .runtime import Contract, Revert


class AccessPolicy(Contract):
    name = "AccessPolicy"
    addr = C.ADDR_ACCESS

    # ------------------------------------------------------------------ 内部
    def _load(self, policy_id: str) -> dict[str, Any]:
        rec = self.state.get(self.key("policy", policy_id))
        if rec is None:
            raise Revert("E_NOT_FOUND", f"授权不存在：{policy_id}")
        return rec

    def _status(self, rec: dict[str, Any]) -> str:
        if rec.get("revoked"):
            return C.ST_REVOKED
        if rec.get("frozen"):
            return C.ST_FROZEN
        if rec["used"] >= rec["max_calls"]:
            return C.ST_EXHAUSTED
        if self.now >= rec["expire_at"]:
            return C.ST_EXPIRED
        return C.ST_ACTIVE

    def _view(self, rec: dict[str, Any], el_names: dict[str, str] | None = None) -> dict[str, Any]:
        st = self._status(rec)
        if el_names is not None and rec["element_id"] in el_names:
            element_name = el_names[rec["element_id"]]
        else:
            element_name = (self.ctx.state.get(self.key_element(rec["element_id"])) or {}).get(
                "name", rec["element_id"]
            )
        return {
            **rec,
            "state": st,
            "remaining": max(0, rec["max_calls"] - rec["used"]),
            "consumer_name": self.ctx.engine.names.get(rec["consumer"], "未登记主体"),
            "element_name": element_name,
        }

    def _element_names(self) -> dict[str, str]:
        """一次性取回元件名，避免列表视图逐条回查注册表（N+1 读）。"""
        return {
            r["id"]: r["name"]
            for r in self.state.scan("DataElementRegistry:element:")
            if "id" in r
        }

    @staticmethod
    def key_element(element_id: str) -> str:
        return f"DataElementRegistry:element:{element_id}"

    # ------------------------------------------------------------------ 授权
    def m_grant(
        self,
        element_id: str,
        consumer: str,
        purpose: str,
        max_calls: int = 10,
        ttl_seconds: int = 86400,
        price_micro: int | None = None,
        purpose_whitelist: list[str] | None = None,
    ) -> dict[str, Any]:
        element = self.call(C.ADDR_REGISTRY, "getElement", {"element_id": element_id})
        self.require(element["status"] == "ACTIVE", "E_FROZEN", "元件处于冻结状态，禁止新增授权")
        self.require(
            self.ctx.sender == element["owner"] or C.ROLE_ADMIN in self.ctx.engine.roles.get(self.ctx.sender, []),
            "E_OWNER",
            "只有元件持有者可签发授权",
        )
        self.require(consumer != element["owner"], "E_SELF", "不得向自身授权")
        self.require(bool(purpose), "E_PURPOSE", "必须声明使用目的")
        self.require(int(max_calls) >= 1, "E_CALLS", "调用次数上限至少为 1")
        self.require(int(ttl_seconds) >= 1, "E_TTL", "有效期必须为正")

        pid = "0x" + sha256_hex(f"{element_id}|{consumer}|{purpose}|{self.now}|{max_calls}")[:16]
        rec = {
            "policy_id": pid,
            "element_id": element_id,
            "owner": element["owner"],
            "consumer": consumer,
            "purpose": purpose,
            "purpose_whitelist": purpose_whitelist or [purpose],
            "max_calls": int(max_calls),
            "used": 0,
            "price_micro": int(price_micro if price_micro is not None else element["price_micro"]),
            "created_at": self.now,
            "expire_at": self.now + int(ttl_seconds),
            "revoked": False,
            "frozen": False,
            "settled_micro": 0,
        }
        self.state.set(self.key("policy", pid), rec)
        self.emit(
            "AccessGranted",
            {
                "policy_id": pid,
                "element_id": element_id,
                "consumer": consumer,
                "purpose": purpose,
                "max_calls": int(max_calls),
                "expire_at": rec["expire_at"],
                "price_micro": rec["price_micro"],
            },
        )
        return self._view(rec)

    # ------------------------------------------------------------------ 消费
    def m_consume(self, policy_id: str, purpose: str = "") -> dict[str, Any]:
        rec = self._load(policy_id)
        self.require(self.ctx.sender == rec["consumer"], "E_CONSUMER", "仅授权使用方可调用")
        st = self._status(rec)
        if st != C.ST_ACTIVE:
            code = {
                C.ST_EXHAUSTED: "E_EXHAUSTED",
                C.ST_EXPIRED: "E_EXPIRED",
                C.ST_REVOKED: "E_REVOKED",
                C.ST_FROZEN: "E_FROZEN",
            }.get(st, "E_STATE")
            raise Revert(code, f"授权状态为 {st}，拒绝访问")
        if purpose and purpose not in rec["purpose_whitelist"]:
            raise Revert("E_PURPOSE_MISMATCH", f"声明用途「{purpose}」不在授权白名单内")

        rec["used"] += 1
        n = rec["used"]
        self.state.set(self.key("policy", policy_id), rec)

        # 数据-调用台账：记录"第几次调用、谁、什么目的、花了多少"，供事后追溯
        self.state.set(
            self.key("usage", f"{policy_id}:{n:05d}"),
            {
                "policy_id": policy_id,
                "element_id": rec["element_id"],
                "consumer": rec["consumer"],
                "seq": n,
                "purpose": purpose or rec["purpose"],
                "ts": self.now,
                "price_micro": rec["price_micro"],
            },
        )

        settle = None
        if rec["price_micro"] > 0:
            settle = self.call(
                C.ADDR_SETTLEMENT,
                "settle",
                {
                    "element_id": rec["element_id"],
                    "amount_micro": rec["price_micro"],
                    "policy_id": policy_id,
                    "payer": rec["consumer"],
                },
            )
            rec["settled_micro"] = rec.get("settled_micro", 0) + rec["price_micro"]
            self.state.set(self.key("policy", policy_id), rec)

        self.emit(
            "AccessConsumed",
            {
                "policy_id": policy_id,
                "element_id": rec["element_id"],
                "consumer": rec["consumer"],
                "seq": n,
                "remaining": max(0, rec["max_calls"] - n),
                "price_micro": rec["price_micro"],
                "allocation": (settle or {}).get("parts", []),
            },
        )
        return {"policy": self._view(rec), "settlement": settle}

    # ------------------------------------------------------------------ 校验（只读）
    def m_check(self, policy_id: str, purpose: str = "") -> dict[str, Any]:
        rec = self._load(policy_id)
        st = self._status(rec)
        ok = st == C.ST_ACTIVE and (not purpose or purpose in rec["purpose_whitelist"])
        return {
            "ok": ok,
            "state": st,
            "remaining": max(0, rec["max_calls"] - rec["used"]),
            "purpose_ok": (not purpose) or purpose in rec["purpose_whitelist"],
        }

    # ------------------------------------------------------------------ 撤销 / 冻结
    def m_revoke(self, policy_id: str, reason: str = "") -> dict[str, Any]:
        rec = self._load(policy_id)
        roles = self.ctx.engine.roles.get(self.ctx.sender, [])
        self.require(
            self.ctx.sender in (rec["owner"], rec["consumer"]) or C.ROLE_ADMIN in roles,
            "E_AUTH",
            "仅授权方、被授权方或治理方可撤销",
        )
        rec["revoked"] = True
        rec["revoked_at"] = self.now
        rec["revoke_reason"] = reason
        self.state.set(self.key("policy", policy_id), rec)
        self.emit("AccessRevoked", {"policy_id": policy_id, "reason": reason, "by": self.ctx.sender})
        return self._view(rec)

    def m_freezeByLeak(self, element_id: str, case_id: str = "") -> int:
        """由 TraceAndFreeze 合约在命中泄漏时调用，冻结该元件全部在效授权。"""
        self.require(self.ctx.caller == C.ADDR_TRACE, "E_AUTH", "仅追踪合约可调用")
        n = 0
        for rec in self.state.scan(self.key("policy", "")):
            if rec["element_id"] == element_id and not rec["frozen"] and not rec["revoked"]:
                rec["frozen"] = True
                rec["frozen_reason"] = f"泄漏告警 {case_id}"
                self.state.set(self.key("policy", rec["policy_id"]), rec)
                n += 1
        self.emit("PoliciesFrozen", {"element_id": element_id, "count": n, "case_id": case_id})
        return n

    # ------------------------------------------------------------------ 查询
    def m_getPolicy(self, policy_id: str) -> dict[str, Any]:
        return self._view(self._load(policy_id))

    def m_listPolicies(self, element_id: str = "", consumer: str = "", limit: int = 200) -> list[dict[str, Any]]:
        items = self.state.scan(self.key("policy", ""))
        if element_id:
            items = [p for p in items if p["element_id"] == element_id]
        if consumer:
            items = [p for p in items if p["consumer"] == consumer]
        items.sort(key=lambda p: p["created_at"], reverse=True)
        names = self._element_names()
        return [self._view(p, names) for p in self.paginate(items, limit)]

    def m_usageLedger(self, policy_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        items = self.state.scan(self.key("usage", ""))
        if policy_id:
            items = [u for u in items if u["policy_id"] == policy_id]
        items.sort(key=lambda u: u["ts"], reverse=True)
        return self.paginate(items, limit)

    def m_consumersOf(self, element_id: str) -> list[dict[str, Any]]:
        """泄漏时用于缩小排查范围：所有曾获得该元件授权的主体。"""
        out = []
        for p in self.state.scan(self.key("policy", "")):
            if p["element_id"] == element_id:
                out.append(
                    {
                        "consumer": p["consumer"],
                        "consumer_name": self.ctx.engine.names.get(p["consumer"], "未登记主体"),
                        "policy_id": p["policy_id"],
                        "used": p["used"],
                        "state": self._status(p),
                    }
                )
        return out
