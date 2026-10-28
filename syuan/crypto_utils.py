"""哈希、规范化序列化、Merkle 树与 secp256k1 签名。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

_CURVE = ec.SECP256K1()
_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def canonical(obj: Any) -> bytes:
    """规范化 JSON 序列化：键排序、无空白、UTF-8。任何节点重算结果一致。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    return sha256_hex(canonical(obj))


def merkle_root(leaves: list[str]) -> str:
    """标准二叉 Merkle 根；空树返回 64 个 0。"""
    if not leaves:
        return "0" * 64
    layer = list(leaves)
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        layer = [sha256_hex(layer[i] + layer[i + 1]) for i in range(0, len(layer), 2)]
    return layer[0]


def merkle_proof(leaves: list[str], index: int) -> list[tuple[str, bool]]:
    """返回 [(sibling_hash, is_left)]，用于轻节点验证某笔交易被打包。"""
    if not leaves:
        raise ValueError("empty leaves")
    layer = list(leaves)
    idx = index
    proof: list[tuple[str, bool]] = []
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        sib = idx ^ 1
        proof.append((layer[sib], sib < idx))
        layer = [sha256_hex(layer[i] + layer[i + 1]) for i in range(0, len(layer), 2)]
        idx //= 2
    return proof


def verify_merkle_proof(leaf: str, proof: Iterable[tuple[str, bool]], root: str) -> bool:
    h = leaf
    for sib, sib_is_left in proof:
        h = sha256_hex(sib + h) if sib_is_left else sha256_hex(h + sib)
    return h == root


class Wallet:
    """secp256k1 账户。地址 = sha256(公钥) 后 20 字节，避免直接暴露公钥哈希前缀。"""

    __slots__ = ("_priv", "pub_bytes", "address")

    def __init__(self, priv: ec.EllipticCurvePrivateKey):
        self._priv = priv
        self.pub_bytes = priv.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint
        )
        self.address = "0x" + sha256_hex(self.pub_bytes)[-40:]

    @classmethod
    def generate(cls) -> "Wallet":
        return cls(ec.generate_private_key(_CURVE))

    @classmethod
    def from_seed(cls, seed: str) -> "Wallet":
        """由固定种子派生密钥，便于测试网复现（生产环境应使用硬件密钥）。"""
        k = int(sha256_hex(seed), 16) % (_ORDER - 1) + 1
        return cls(ec.derive_private_key(k, _CURVE))

    def sign(self, data: bytes) -> str:
        der = self._priv.sign(data, ec.ECDSA(hashes.SHA256()))
        return der.hex()

    def public_pem(self) -> bytes:
        return self._priv.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )


def verify_signature(pub_pem: bytes, data: bytes, sig_hex: str) -> bool:
    try:
        pub = serialization.load_pem_public_key(pub_pem)
        pub.verify(bytes.fromhex(sig_hex), data, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


def short(addr: str, n: int = 6) -> str:
    if not addr or len(addr) <= 2 * n + 2:
        return addr
    return f"{addr[: n + 2]}…{addr[-n:]}"


def recoverable_nonce(*parts: str) -> int:
    return int(sha256_hex("|".join(parts))[:8], 16)


__all__ = [
    "canonical",
    "sha256_hex",
    "hash_obj",
    "merkle_root",
    "merkle_proof",
    "verify_merkle_proof",
    "Wallet",
    "verify_signature",
    "short",
    "decode_dss_signature",
]
