"""联盟链内核：交易、区块、Merkle 承诺与多签共识。

设计取舍（已在设计报告第 4 章说明）：
  * 4 个验证节点、门限 3（3f+1 容错，f = 1）。
  * 轮流出块（round-robin），领导者离线时自动顺延到下一位在线节点 —— 即最简 view-change。
  * 区块需 >= 3 个验证节点签名方可落账；在线节点不足门限时**主动停止出块**（CP 优先），
    宁可不可用也不产生分叉，这符合"权益与资金流转"场景对一致性的要求。
  * 状态根 state_root 由确定性合约执行产生，任一节点可全量重放校验。
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config as C
from .crypto_utils import (
    Wallet,
    canonical,
    hash_obj,
    merkle_proof,
    merkle_root,
    sha256_hex,
    verify_merkle_proof,
    verify_signature,
)


class ChainError(Exception):
    """链层异常。"""


class ConsensusError(ChainError):
    """共识无法达成（在线节点不足门限）。"""


class TxRejected(ChainError):
    """交易校验失败（签名 / nonce / 格式）。"""


# --------------------------------------------------------------------------- 交易
def build_tx(
    wallet: Wallet,
    to: str,
    method: str,
    args: dict[str, Any] | None = None,
    nonce: int = 0,
    gas_limit: int = 2_000_000,
) -> dict[str, Any]:
    body = {
        "sender": wallet.address,
        "to": to,
        "method": method,
        "args": args or {},
        "nonce": int(nonce),
        "ts": round(time.time(), 6),
        "gas_limit": int(gas_limit),
    }
    txid = hash_obj(body)
    sig = wallet.sign(bytes.fromhex(txid))
    return {
        "txid": txid,
        "type": "call",
        **body,
        "pubkey": wallet.public_pem().hex(),
        "sig": sig,
    }


def verify_tx(tx: dict[str, Any]) -> bool:
    body = {k: tx[k] for k in ("sender", "to", "method", "args", "nonce", "ts", "gas_limit")}
    if hash_obj(body) != tx["txid"]:
        return False
    if tx.get("type") != "call":
        return False
    return verify_signature(bytes.fromhex(tx["pubkey"]), bytes.fromhex(tx["txid"]), tx["sig"])


# --------------------------------------------------------------------------- 节点
@dataclass
class ValidatorNode:
    node_id: str
    wallet: Wallet
    online: bool = True
    blocks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def address(self) -> str:
        return self.wallet.address

    @property
    def height(self) -> int:
        return len(self.blocks)


def make_validators(count: int = C.VALIDATOR_COUNT) -> list[ValidatorNode]:
    """测试网固定密钥（种子公开，仅用于可复现演示；生产环境为各机构独立 HSM 密钥）。"""
    return [
        ValidatorNode(f"validator-{i}", Wallet.from_seed(f"shuyuanlian/validator/{i}"))
        for i in range(1, count + 1)
    ]


# --------------------------------------------------------------------------- 链
class ConsortiumChain:
    def __init__(
        self,
        validators: list[ValidatorNode] | None = None,
        quorum: int = C.QUORUM,
        on_commit: Callable[[dict], None] | None = None,
    ):
        self.validators = validators or make_validators()
        self.quorum = quorum
        self.on_commit = on_commit
        self._nonce: dict[str, int] = {}

    # ---------------- 查询
    @property
    def head(self) -> dict[str, Any] | None:
        b = self.validators[0].blocks
        return b[-1] if b else None

    @property
    def height(self) -> int:
        return self.validators[0].height

    def online_validators(self) -> list[ValidatorNode]:
        return [v for v in self.validators if v.online]

    def tx_count(self) -> int:
        return sum(len(b["txs"]) for b in self.validators[0].blocks)

    # ---------------- 出块
    def _next_proposer(self) -> ValidatorNode:
        online = self.online_validators()
        if len(online) < self.quorum:
            raise ConsensusError(
                f"在线验证节点 {len(online)} < 门限 {self.quorum}，共识不可用，停止出块（CP 优先）"
            )
        return online[self.height % len(online)]

    def commit(self, txs: list[dict[str, Any]], state_root: str, gas_used: int = 0) -> dict[str, Any]:
        if not txs:
            raise ChainError("空区块不予提交")
        for tx in txs:
            if not verify_tx(tx):
                raise TxRejected(f"交易签名或哈希校验失败：{tx.get('txid', '?')[:16]}")

        proposer = self._next_proposer()
        head = self.head
        block = {
            "height": self.height,
            "prev_hash": head["block_hash"] if head else "0" * 64,
            "merkle_root": merkle_root([t["txid"] for t in txs]),
            "state_root": state_root,
            "tx_count": len(txs),
            "gas_used": int(gas_used),
            "ts": round(time.time(), 6),
            "proposer": proposer.node_id,
            "txs": txs,
        }
        block["block_hash"] = hash_obj({k: v for k, v in block.items() if k not in ("txs", "block_hash")})

        sigs: dict[str, str] = {}
        for node in self.online_validators():
            sigs[node.node_id] = node.wallet.sign(bytes.fromhex(block["block_hash"]))
        block["sigs"] = sigs
        if len(sigs) < self.quorum:
            raise ConsensusError("签名数未达门限")

        for node in self.online_validators():
            node.blocks.append(copy.deepcopy(block))
        if self.on_commit:
            self.on_commit(block)
        return block

    # ---------------- 校验
    def verify_block(self, block: dict[str, Any], prev: dict[str, Any] | None) -> tuple[bool, str]:
        expected_prev = prev["block_hash"] if prev else "0" * 64
        if block["prev_hash"] != expected_prev:
            return False, "prev_hash 不匹配"
        if prev and block["height"] != prev["height"] + 1:
            return False, "区块高度不连续"
        if merkle_root([t["txid"] for t in block["txs"]]) != block["merkle_root"]:
            return False, "Merkle 根不匹配（交易被篡改）"
        for t in block["txs"]:
            body = {k: t[k] for k in ("sender", "to", "method", "args", "nonce", "ts", "gas_limit")}
            if hash_obj(body) != t["txid"]:
                return False, f"交易内容被篡改：{t['txid'][:12]}…"
        body = {k: v for k, v in block.items() if k not in ("txs", "sigs", "block_hash")}
        if hash_obj(body) != block["block_hash"]:
            return False, "区块头哈希不匹配"
        sigs = block.get("sigs", {})
        if len(sigs) < self.quorum:
            return False, f"签名数 {len(sigs)} 低于门限 {self.quorum}"
        pubs = {v.node_id: v.wallet.public_pem() for v in self.validators}
        for nid, sig in sigs.items():
            if nid not in pubs or not verify_signature(pubs[nid], bytes.fromhex(block["block_hash"]), sig):
                return False, f"节点 {nid} 签名无效"
        return True, "ok"

    def integrity(self) -> dict[str, Any]:
        prev = None
        for b in self.validators[0].blocks:
            ok, why = self.verify_block(b, prev)
            if not ok:
                return {"ok": False, "height": b["height"], "reason": why}
            prev = b
        return {"ok": True, "blocks": self.height, "txs": self.tx_count()}

    def inclusion_proof(self, height: int, tx_index: int = 0) -> dict[str, Any]:
        b = self.validators[0].blocks[height]
        leaves = [t["txid"] for t in b["txs"]]
        return {
            "txid": leaves[tx_index],
            "proof": merkle_proof(leaves, tx_index),
            "root": b["merkle_root"],
            "valid": verify_merkle_proof(leaves[tx_index], merkle_proof(leaves, tx_index), b["merkle_root"]),
        }

    # ---------------- 视图
    def recent(self, limit: int = 8) -> list[dict[str, Any]]:
        blocks = self.validators[0].blocks[-limit:]
        out = []
        for b in reversed(blocks):
            out.append(
                {
                    "height": b["height"],
                    "hash": b["block_hash"],
                    "prev": b["prev_hash"],
                    "merkle": b["merkle_root"],
                    "state_root": b["state_root"],
                    "tx_count": b["tx_count"],
                    "gas_used": b["gas_used"],
                    "ts": b["ts"],
                    "proposer": b["proposer"],
                    "sigs": len(b["sigs"]),
                    "txs": [
                        {
                            "txid": t["txid"],
                            "sender": t["sender"],
                            "method": t["method"],
                            "to": t["to"],
                            "args": t["args"],
                        }
                        for t in b["txs"]
                    ],
                }
            )
        return out

    def network_status(self) -> dict[str, Any]:
        return {
            "validators": [
                {
                    "node_id": v.node_id,
                    "address": v.address,
                    "online": v.online,
                    "height": v.height,
                }
                for v in self.validators
            ],
            "quorum": self.quorum,
            "height": self.height,
            "online": len(self.online_validators()),
        }

    def new_nonce(self, addr: str) -> int:
        n = self._nonce.get(addr, 0)
        self._nonce[addr] = n + 1
        return n

    def sync_node(self, node_id: str) -> int:
        """节点恢复后追块。"""
        node = next(v for v in self.validators if v.node_id == node_id)
        node.online = True
        missing = self.validators[0].blocks[len(node.blocks) :]
        node.blocks.extend(copy.deepcopy(missing))
        return len(missing)

    # ---------------- 序列化（供演示台持久化 / 审计导出）
    def export(self) -> dict[str, Any]:
        return {
            "chain_id": "shuyuanlian-demo-1",
            "exported_at": time.time(),
            "consensus": f"quorum-{self.quorum}-of-{len(self.validators)}",
            "blocks": copy.deepcopy(self.validators[0].blocks),
        }


__all__ = [
    "ConsortiumChain",
    "ValidatorNode",
    "make_validators",
    "build_tx",
    "verify_tx",
    "ChainError",
    "ConsensusError",
    "TxRejected",
    "canonical",
    "sha256_hex",
    "uuid",
]
