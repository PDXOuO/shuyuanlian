"""Paillier 加法同态加密（纯 Python 实现，无第三方密码库依赖）。

用途：多家机构在不暴露各自原始数值的前提下，由链上/链下聚合方求和后
只解出统计量（均值），任何单方密文都不足以还原他方明文。

安全参数：演示默认 1024 bit 模数（等价约 80 bit 安全强度），
生产部署应使用 2048/3072 bit，接口不变。
"""

from __future__ import annotations

import json
import math
import secrets
from pathlib import Path

_SMALL_PRIMES = [
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
    73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151,
]


def _is_probable_prime(n: int, rounds: int = 32) -> bool:
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def generate_prime(bits: int) -> int:
    while True:
        cand = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if any(cand % p == 0 for p in _SMALL_PRIMES):
            continue
        if _is_probable_prime(cand):
            return cand


class PaillierPublicKey:
    def __init__(self, n: int, g: int):
        self.n = n
        self.g = g
        self.n2 = n * n
        self.bits = n.bit_length()

    def encrypt(self, m: int, r: int | None = None) -> int:
        if not 0 <= m < self.n:
            raise ValueError("明文需在 [0, n) 范围内")
        r = r or secrets.randbelow(self.n - 1) + 1
        while math.gcd(r, self.n) != 1:
            r = secrets.randbelow(self.n - 1) + 1
        return (pow(self.g, m, self.n2) * pow(r, self.n, self.n2)) % self.n2

    def add(self, c1: int, c2: int) -> int:
        return (c1 * c2) % self.n2

    def add_const(self, c: int, k: int) -> int:
        return (c * pow(self.g, k, self.n2)) % self.n2

    def mul_const(self, c: int, k: int) -> int:
        return pow(c, k, self.n2)

    def to_dict(self) -> dict:
        return {"n": hex(self.n), "g": hex(self.g)}

    @staticmethod
    def from_dict(d: dict) -> "PaillierPublicKey":
        return PaillierPublicKey(int(d["n"], 16), int(d["g"], 16))


class PaillierPrivateKey:
    def __init__(self, pub: PaillierPublicKey, lam: int, mu: int):
        self.pub = pub
        self.lam = lam
        self.mu = mu

    @property
    def n(self) -> int:
        return self.pub.n

    @property
    def n2(self) -> int:
        return self.pub.n2

    def decrypt(self, c: int) -> int:
        u = pow(c, self.lam, self.n2)
        l = (u - 1) // self.n
        return (l * self.mu) % self.n

    def to_dict(self) -> dict:
        return {"n": hex(self.pub.n), "g": hex(self.pub.g), "lam": hex(self.lam), "mu": hex(self.mu)}

    @staticmethod
    def from_dict(d: dict) -> "PaillierPrivateKey":
        return PaillierPrivateKey(PaillierPublicKey(int(d["n"], 16), int(d["g"], 16)), int(d["lam"], 16), int(d["mu"], 16))


def generate_keypair(bits: int = 1024) -> PaillierPrivateKey:
    half = bits // 2
    while True:
        p = generate_prime(half)
        q = generate_prime(half)
        if p == q:
            continue
        n = p * q
        if n.bit_length() != bits:
            continue
        lam = (p - 1) * (q - 1) // math.gcd(p - 1, q - 1)
        g = n + 1
        try:
            mu = pow(lam, -1, n)
        except ValueError:
            continue
        return PaillierPrivateKey(PaillierPublicKey(n, g), lam, mu)


def load_or_create_keypair(path: Path, bits: int = 1024) -> PaillierPrivateKey:
    if path.exists():
        try:
            return PaillierPrivateKey.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    kp = generate_keypair(bits)
    path.write_text(json.dumps(kp.to_dict()), encoding="utf-8")
    return kp


__all__ = [
    "PaillierPublicKey",
    "PaillierPrivateKey",
    "generate_keypair",
    "load_or_create_keypair",
]
