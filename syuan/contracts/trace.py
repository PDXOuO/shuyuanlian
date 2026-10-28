"""TraceAndFreeze —— 指纹溯源、泄漏取证与链上冻结。

链在这里承担的不是"存个证据"，而是**规则的强制执行**：
检测机构（预言机）提交命中结果后，合约自动冻结该元件及其全部在效授权，
并把"曾经获得过授权的全部主体"作为排查清单留痕 —— 这一步数据库给不出
多方都认的结论。
"""

from __future__ import annotations

from typing import Any

from .. import config as C
from ..crypto_utils import sha256_hex
from .runtime import Contract, Revert


class TraceAndFreeze(Contract):
    name = "TraceAndFreeze"
    addr = C.ADDR_TRACE

    # ------------------------------------------------------------------ 指纹登记
    def m_registerFingerprint(
        self,
        element_id: str,
        code_hex: str,
        quantum: float = C.FINGERPRINT_QUANTUM,
        bits: int = C.FINGERPRINT_BITS,
        rows: int = 0,
        columns: list[str] | None = None,
    ) -> dict[str, Any]:
        el = self.call(C.ADDR_REGISTRY, "getElement", {"element_id": element_id})
        self.require(self.ctx.sender == el["owner"], "E_OWNER", "仅元件持有者可登记指纹")
        self.require(len(code_hex) >= 8, "E_CODE", "指纹码至少 32 bit")
        rec = {
            "element_id": element_id,
            "code_hex": code_hex,
            "quantum": float(quantum),
            "bits": int(bits),
            "rows": int(rows),
            "columns": columns or [],
            "registered_at": self.now,
            "registered_by": self.ctx.sender,
        }
        self.state.set(self.key("fp", element_id), rec)
        self.emit(
            "FingerprintRegistered",
            {"element_id": element_id, "code_hex": code_hex, "bits": int(bits), "quantum": float(quantum)},
        )
        return rec

    def m_fingerprint(self, element_id: str) -> dict[str, Any] | None:
        return self.state.get(self.key("fp", element_id))

    # ------------------------------------------------------------------ 泄漏上报
    def m_reportLeak(
        self,
        element_id: str,
        similarity_bp: int,
        evidence_hash: str,
        sample_source: str = "",
        operator: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        self.ctx.require_role(C.ROLE_ORACLE)
        fp = self.state.get(self.key("fp", element_id))
        self.require(fp is not None, "E_NO_FP", "该元件未登记指纹，无法溯源")
        self.require(int(similarity_bp) >= C.TRACE_HIT_BP,
                     "E_LOW_CONF", f"相似度 {similarity_bp/100:.1f}% 低于判定门限 {C.TRACE_HIT_BP/100:.1f}%")
        self.require(len(evidence_hash) == 64, "E_EVIDENCE", "证据哈希必须是 sha256")
        self.require(self.state.get(self.key("case", element_id)) is None, "E_DUP_CASE", "该元件已有在办案件")

        case_id = "CASE-" + sha256_hex(f"{element_id}|{evidence_hash}|{self.now}")[:10].upper()
        consumers = self.call(C.ADDR_ACCESS, "consumersOf", {"element_id": element_id})

        frozen_policies = self.call(C.ADDR_ACCESS, "freezeByLeak", {"element_id": element_id, "case_id": case_id})
        self.call(C.ADDR_REGISTRY, "freezeElement", {"element_id": element_id, "reason": f"泄漏告警 {case_id}"})

        case = {
            "case_id": case_id,
            "element_id": element_id,
            "element_name": (self.state.get(f"DataElementRegistry:element:{element_id}") or {}).get("name", element_id),
            "similarity_bp": int(similarity_bp),
            "evidence_hash": evidence_hash,
            "sample_source": sample_source,
            "operator": operator,
            "note": note,
            "consumers": consumers,
            "frozen_policies": frozen_policies,
            "reported_by": self.ctx.sender,
            "created_at": self.now,
            "status": "FROZEN",
        }
        self.state.set(self.key("case", element_id), case)
        self.state.set(self.key("caseIndex", case_id), element_id)
        self.emit(
            "LeakDetected",
            {
                "case_id": case_id,
                "element_id": element_id,
                "element_name": case["element_name"],
                "similarity_bp": int(similarity_bp),
                "frozen_policies": frozen_policies,
                "suspects": [
                    {"name": c["consumer_name"], "address": c["consumer"], "used": c["used"]} for c in consumers
                ],
                "sample_source": sample_source,
            },
        )
        return case

    # ------------------------------------------------------------------ 查询
    def m_cases(self, limit: int = 50) -> list[dict[str, Any]]:
        items = self.state.scan(self.key("case", ""))
        # caseIndex 是另一个前缀，这里 key("case", "") 只匹配 "TraceAndFreeze:case:"
        items.sort(key=lambda c: c["created_at"], reverse=True)
        return self.paginate(items, limit)

    def m_case(self, case_id: str) -> dict[str, Any]:
        eid = self.state.get(self.key("caseIndex", case_id))
        if not eid:
            raise Revert("E_NOT_FOUND", f"案件不存在：{case_id}")
        return self.state.get(self.key("case", eid))
