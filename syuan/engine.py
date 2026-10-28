"""执行引擎：把交易、合约、状态、事件与联盟链组装成一台可运行的"链"。

执行模型（简化但确定性可验证）：
    交易 → 校验签名 → 快照状态 → 合约执行（可跨合约内部调用）
         → 失败则整体回滚（交易仍然打包上链并留痕，与 EVM 语义一致）
         → 计算状态根 → 收集事件 → 提交区块（>=3/4 节点签名）
任一节点可重放全部区块并逐块比对状态根，验证执行确定性。
"""

from __future__ import annotations

import time
from typing import Any

from . import config as C
from .contracts import (
    AccessPolicy,
    AggregateAnchor,
    DataElementRegistry,
    Revert,
    Settlement,
    StateStore,
    TraceAndFreeze,
)
from .contracts.runtime import ExecContext
from .crypto_utils import short
from .ledger import ConsortiumChain, ConsensusError, build_tx

# 联盟成员（脱敏表述，不含真实机构名）
ORG = {
    "admin": "某市公共数据授权运营中心",
    "park": "某工业园区运营方",
    "stat": "某市统计部门",
    "processor": "某数据加工服务商",
    "bank": "某商业银行",
    "oracle": "第三方数据溯源机构",
    "reseller": "某数据服务商",
}

PARTICIPANTS: list[tuple[str, str, list[str]]] = [
    ("admin", ORG["admin"], [C.ROLE_ADMIN]),
    ("park", ORG["park"], [C.ROLE_OWNER]),
    ("stat", ORG["stat"], [C.ROLE_OWNER]),
    ("processor", ORG["processor"], [C.ROLE_OWNER, C.ROLE_CONSUMER]),
    ("bank", ORG["bank"], [C.ROLE_CONSUMER]),
    ("oracle", ORG["oracle"], [C.ROLE_ORACLE]),
    ("reseller", ORG["reseller"], [C.ROLE_CONSUMER]),
]

GAS_TX_BASE = 21_000
GAS_WRITE = 5_000
GAS_READ = 200
GAS_EVENT = 375


class ChainEngine:
    def __init__(self, bootstrap: bool = True):
        from .crypto_utils import Wallet

        self.wallets = {k: Wallet.from_seed(f"shuyuanlian/org/{k}") for k, _, _ in PARTICIPANTS}
        self.state = StateStore()
        self.chain = ConsortiumChain()
        self.roles: dict[str, list[str]] = {self.wallets["admin"].address: [C.ROLE_ADMIN]}
        self.names: dict[str, str] = {self.wallets["admin"].address: ORG["admin"]}

        self.contracts = {
            C.ADDR_REGISTRY: DataElementRegistry,
            C.ADDR_ACCESS: AccessPolicy,
            C.ADDR_SETTLEMENT: Settlement,
            C.ADDR_TRACE: TraceAndFreeze,
            C.ADDR_ANCHOR: AggregateAnchor,
        }
        self.events: list[dict[str, Any]] = []
        self.txlog: list[dict[str, Any]] = []
        self.pending: list[tuple[dict, dict]] = []
        self.block_times: list[tuple[int, float, int]] = []  # (height, ts, tx_count)
        self.created_at = time.time()
        self.gas_total = 0
        self._deferred = False

        if bootstrap:
            self._bootstrap()

    # ------------------------------------------------------------------ 初始化
    def _bootstrap(self) -> None:
        admin = self.wallets["admin"]
        for key, name, roles in PARTICIPANTS:
            self.invoke(
                admin,
                C.ADDR_REGISTRY,
                "registerParticipant",
                {"address": self.wallets[key].address, "name": name, "roles": roles},
                commit=False,
            )
        block = self.flush()
        self.genesis = block

    # ------------------------------------------------------------------ 分发
    def dispatch(self, addr: str, method: str, args: dict, ctx: ExecContext, depth: int = 0) -> Any:
        cls = self.contracts.get(addr)
        if cls is None:
            raise Revert("E_ADDR", f"未知合约地址 {addr}")
        return cls(ctx).dispatch(method, args)

    # ------------------------------------------------------------------ 执行
    def execute(self, tx: dict[str, Any]) -> dict[str, Any]:
        self.state.begin()
        self.state.reset_meters()
        ctx = ExecContext(self.state, tx["sender"], float(tx["ts"]), self)
        try:
            result = self.dispatch(tx["to"], tx["method"], tx["args"], ctx, 0)
            status, code, message = "SUCCESS", "", ""
            events = ctx.events
        except Revert as exc:
            self.state.rollback()
            status, code, message = "REVERTED", exc.code, exc.msg
            result = {"error": exc.code, "message": exc.msg}
            events = []  # 回滚后事件一并丢弃
        else:
            self.state.commit()
        gas = GAS_TX_BASE + GAS_WRITE * self.state.writes + GAS_READ * self.state.reads + GAS_EVENT * len(events)
        self.gas_total += gas

        rec = {
            "txid": tx["txid"],
            "hash": short(tx["txid"], 8),
            "sender": tx["sender"],
            "sender_name": self.names.get(tx["sender"], "未登记主体"),
            "to": tx["to"],
            "contract": C.CONTRACT_NAMES.get(tx["to"], tx["to"]),
            "method": tx["method"],
            "args": tx["args"],
            "status": status,
            "code": code,
            "message": message,
            "gas": gas,
            "ts": tx["ts"],
            "height": None,
            "result": result if isinstance(result, (dict, list, int, str, float)) or result is None else str(result),
        }
        self.txlog.append(rec)
        if len(self.txlog) > 2000:
            self.txlog = self.txlog[-2000:]

        for ev in events:
            self.events.append({**ev, "txid": tx["txid"], "height": None, "seq": len(self.events) + 1})
        if len(self.events) > 5000:
            self.events = self.events[-5000:]

        self.pending.append((tx, rec))
        return rec

    # ------------------------------------------------------------------ 提交
    def flush(self) -> dict[str, Any] | None:
        if not self.pending:
            return None
        txs = [tx for tx, _ in self.pending]
        gas = sum(rec["gas"] for _, rec in self.pending)
        root = self.state.root()
        try:
            block = self.chain.commit(txs, root, gas)
        except ConsensusError:
            raise
        height = block["height"]
        txids = {t["txid"] for t in txs}
        for tx, rec in self.pending:
            rec["height"] = height
        for ev in self.events:
            if ev["height"] is None and ev["txid"] in txids:
                ev["height"] = height
        self.pending.clear()
        self.block_times.append((height, block["ts"], block["tx_count"]))
        return block

    # ------------------------------------------------------------------ 对外接口
    def defer(self, on: bool = True) -> None:
        """批处理模式：交易先入池，由调用方在业务动作结束时统一出块。"""
        self._deferred = on

    def invoke(self, wallet, addr: str, method: str, args: dict, commit: bool = True) -> dict[str, Any]:
        tx = build_tx(wallet, addr, method, args, nonce=self.chain.new_nonce(wallet.address))
        rec = self.execute(tx)
        if commit and not self._deferred:
            block = self.flush()
            if block:
                rec["height"] = block["height"]
        return rec

    def invoke_as(self, key: str, addr: str, method: str, args: dict, commit: bool = True) -> dict[str, Any]:
        return self.invoke(self.wallets[key], addr, method, args, commit=commit)

    def call(self, addr: str, method: str, args: dict | None = None, sender: str | None = None) -> Any:
        """只读调用：执行后回滚，不产生交易。"""
        self.state.begin()
        ctx = ExecContext(self.state, sender or self.wallets["admin"].address, time.time(), self)
        try:
            return self.dispatch(addr, method, args or {}, ctx, 0)
        finally:
            self.state.rollback()

    def addr(self, key: str) -> str:
        return self.wallets[key].address

    def name_of(self, addr: str) -> str:
        return self.names.get(addr, short(addr))

    # ------------------------------------------------------------------ 视图
    def stats(self) -> dict[str, Any]:
        elapsed = max(time.time() - self.created_at, 1e-6)
        txs = self.chain.tx_count()
        last = self.block_times[-1] if self.block_times else (0, time.time(), 0)
        prev = self.block_times[-2] if len(self.block_times) > 1 else None
        interval = (last[1] - prev[1]) if prev else 0.0
        return {
            "height": self.chain.height,
            "blocks": self.chain.height,
            "txs": txs,
            "avg_tps": round(txs / elapsed, 2),
            "avg_block_ms": round(
                (self.block_times[-1][1] - self.block_times[0][1]) / max(len(self.block_times) - 1, 1) * 1000, 1
            ) if len(self.block_times) > 1 else 0.0,
            "last_block": {
                "height": last[0],
                "tx_count": last[2],
                "interval_ms": round(interval * 1000, 2),
                "tps": round(last[2] / interval, 1) if interval > 0 else None,
            },
            "gas_total": self.gas_total,
            "elapsed_s": round(elapsed, 2),
            "online": len(self.chain.online_validators()),
            "quorum": self.chain.quorum,
        }

    def elements(self) -> list[dict[str, Any]]:
        # 展示字段统一新建字典，不回写合约返回的记录
        return [
            {**it, "owner_name": self.name_of(it["owner"]), "short_hash": it["content_hash"][:16]}
            for it in (self.call(C.ADDR_REGISTRY, "listElements", {}) or [])
        ]

    def policies(self) -> list[dict[str, Any]]:
        return self.call(C.ADDR_ACCESS, "listPolicies", {}) or []

    def balances(self) -> list[dict[str, Any]]:
        return self.call(C.ADDR_SETTLEMENT, "balances", {}) or []

    def cases(self) -> list[dict[str, Any]]:
        return self.call(C.ADDR_TRACE, "cases", {}) or []

    def participants(self) -> list[dict[str, Any]]:
        return [
            {**it, "short": short(it["address"])}
            for it in (self.call(C.ADDR_REGISTRY, "participants", {}) or [])
        ]

    def settlement_stats(self) -> dict[str, Any]:
        return self.call(C.ADDR_SETTLEMENT, "stats", {}) or {}

    def recent_events(self, limit: int = 40) -> list[dict[str, Any]]:
        return self.events[-limit:][::-1]

    def snapshot(self, blocks: int = 6, events: int = 40) -> dict[str, Any]:
        if not hasattr(self, "_integrity_cache") or self._integrity_cache[0] != self.chain.height:
            self._integrity_cache = (self.chain.height, self.chain.integrity())
        integrity = self._integrity_cache[1]
        return {
            "stats": self.stats(),
            "network": self.chain.network_status(),
            "integrity": integrity,
            "elements": self.elements(),
            "policies": self.policies(),
            "balances": self.balances(),
            "settlement": self.settlement_stats(),
            "cases": self.cases(),
            "participants": self.participants(),
            "events": self.recent_events(events),
            "blocks": self.chain.recent(blocks),
            "txlog": list(reversed(self.txlog[-20:])),
        }

    # ------------------------------------------------------------------ 可验证性
    def replay(self) -> dict[str, Any]:
        """全量重放：从创世区块重新执行每一笔交易，逐块比对状态根。"""
        fresh = ChainEngine(bootstrap=False)
        mismatches: list[dict[str, Any]] = []
        blocks = self.chain.validators[0].blocks
        for b in blocks:
            for tx in b["txs"]:
                fresh.execute(tx)
            root = fresh.state.root()
            if root != b["state_root"]:
                mismatches.append({"height": b["height"], "expected": b["state_root"], "actual": root})
            fresh.flush()
        return {
            "ok": not mismatches,
            "blocks": len(blocks),
            "txs": sum(len(b["txs"]) for b in blocks),
            "final_root": fresh.state.root(),
            "mismatches": mismatches,
        }


__all__ = ["ChainEngine", "ORG", "PARTICIPANTS"]
