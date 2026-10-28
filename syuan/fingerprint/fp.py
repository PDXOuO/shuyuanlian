"""数据元件鲁棒指纹：量化奇偶调制（Quantization Index Parity）。

原理
----
对数值型字段做量化 q_idx = round(v / Δ)，把 q_idx 的奇偶性强制成指纹码的某一位。
嵌入时按"主键键控哈希"决定该行承载哪一位：
        p = HMAC(key, row_id) mod L
因此指纹与行序无关，可抵抗抽样、截断、行乱序；承载列按"内容是否可解析为数值"自动识别，
可抵抗列重排、列改名与字段拼接。

容量与代价
----------
每个数值单元格最多被改动 Δ/2（默认 Δ=0.01），对 10^3~10^4 量级的数据是 1e-6 相对误差；
每位码字获得 rows×carriers/L 次投票，默认 432 行 × 4 列 / 32 位 ≈ 54 票/位。
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .. import config as C


@dataclass
class Table:
    columns: list[str]
    rows: list[list[Any]] = field(default_factory=list)

    def copy(self) -> "Table":
        return Table(list(self.columns), [list(r) for r in self.rows])

    def col(self, name: str) -> int:
        return self.columns.index(name)

    def __len__(self) -> int:
        return len(self.rows)

    def to_csv(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(self.columns)
            w.writerows(self.rows)
        return path

    def content_hash(self) -> str:
        h = hashlib.sha256()
        h.update(("|".join(self.columns)).encode("utf-8"))
        for r in self.rows:
            h.update(("|".join(str(x) for x in r)).encode("utf-8"))
        return h.hexdigest()

    def schema_hash(self) -> str:
        return hashlib.sha256("|".join(sorted(self.columns)).encode("utf-8")).hexdigest()


def _as_float(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def numeric_columns(table: Table) -> list[int]:
    """内容驱动的数值列识别：全部单元格可解析为浮点数才计入（列名无关）。"""
    out = []
    for j in range(len(table.columns)):
        vals = [_as_float(r[j]) for r in table.rows]
        if all(v is not None for v in vals):
            out.append(j)
    return out


class RobustFingerprint:
    def __init__(self, key: bytes | str = b"shuyuanlian/fp/v1", bits: int = C.FINGERPRINT_BITS,
                 quantum: float = C.FINGERPRINT_QUANTUM):
        self.key = key.encode() if isinstance(key, str) else key
        self.bits = bits
        self.quantum = quantum

    # ------------------------------------------------------------------ 码字
    def code_for(self, element_id: str) -> list[int]:
        digest = hmac.new(self.key, f"code|{element_id}".encode(), hashlib.sha256).hexdigest()
        n = int(digest[:16], 16)
        return [(n >> i) & 1 for i in range(self.bits)]

    def _bit_index(self, row_id: str) -> int:
        digest = hmac.new(self.key, f"pos|{row_id}".encode(), hashlib.sha256).hexdigest()
        return int(digest[:8], 16) % self.bits

    @staticmethod
    def bits_to_hex(bits: list[int]) -> str:
        n = 0
        for i, b in enumerate(bits):
            n |= (b & 1) << i
        return f"{n:0{(len(bits) + 3) // 4}x}"

    @staticmethod
    def hamming(a: list[int], b: list[int]) -> int:
        return sum(1 for x, y in zip(a, b) if x != y)

    # ------------------------------------------------------------------ 嵌入
    def embed(self, table: Table, code: list[int], id_column: int = 0) -> Table:
        carriers = [j for j in numeric_columns(table) if j != id_column]
        if not carriers:
            raise ValueError("没有可承载指纹的数值列")
        out = table.copy()
        q = self.quantum
        for i, row in enumerate(out.rows):
            rid = str(row[id_column])
            b = code[self._bit_index(rid)]
            for j in carriers:
                v = float(row[j])
                k = round(v / q)
                if (int(k) & 1) == b:
                    continue
                k = int(k)
                # 选择改动更小的方向
                k = k + 1 if (v - k * q) >= 0 else k - 1
                if k == 0:                      # 保持符号，避免负值被截断为 0
                    k = 1 if v >= 0 else -1
                row[j] = round(k * q, 6)
        return out

    # ------------------------------------------------------------------ 提取
    def _extract_with_id(self, table: Table, id_column: int) -> tuple[list[int], float]:
        carriers = [j for j in numeric_columns(table) if j != id_column]
        if not carriers or not table.rows:
            return [0] * self.bits, 0.0
        ones = np.zeros(self.bits, dtype=np.int64)
        total = np.zeros(self.bits, dtype=np.int64)
        q = self.quantum
        for row in table.rows:
            p = self._bit_index(str(row[id_column]))
            for j in carriers:
                v = float(row[j])
                ones[p] += int(round(v / q)) & 1
                total[p] += 1
        bits = []
        for p in range(self.bits):
            if total[p] == 0:
                bits.append(0)
                continue
            bits.append(1 if ones[p] * 2 > total[p] else 0)
        return bits, float(total.min() if total.size else 0)

    def detect(self, table: Table, expected: list[int]) -> dict[str, Any]:
        """对给定样本尝试所有候选主键列，返回最可能的溯源结论。"""
        best = {
            "similarity_bp": 0,
            "matched": 0,
            "bits": self.bits,
            "id_column": "",
            "code_hex": "0" * ((self.bits + 3) // 4),
            "votes_per_bit": 0.0,
            "candidates": [],
        }
        for ic in range(len(table.columns)):
            bits, votes = self._extract_with_id(table, ic)
            matched = self.bits - self.hamming(bits, expected)
            sim_bp = int(round(matched / self.bits * 10000))
            best["candidates"].append({"id_column": table.columns[ic], "similarity_bp": sim_bp})
            if sim_bp >= best["similarity_bp"]:
                best.update(
                    {
                        "similarity_bp": sim_bp,
                        "matched": matched,
                        "id_column": table.columns[ic],
                        "code_hex": self.bits_to_hex(bits),
                        "votes_per_bit": votes,
                    }
                )
        best["candidates"].sort(key=lambda c: c["similarity_bp"], reverse=True)
        best["candidates"] = best["candidates"][:4]
        return best


# ---------------------------------------------------------------------- 攻击变换
def t_truncate(table: Table, frac: float = 0.4) -> Table:
    """截断：只保留前 frac 比例的行。"""
    n = max(1, int(len(table) * frac))
    return Table(list(table.columns), [list(r) for r in table.rows[:n]])


def t_noise(table: Table, sigma: float = 0.0025, seed: int = 0) -> Table:
    """加噪：对数值列叠加高斯噪声。"""
    rng = random.Random(seed)
    t = table.copy()
    for j in numeric_columns(t):
        for r in t.rows:
            r[j] = round(float(r[j]) + rng.gauss(0, sigma), 6)
    return t


def t_sample(table: Table, frac: float = 0.5, seed: int = 0) -> Table:
    """抽样：随机抽取 frac 比例的行。"""
    rng = random.Random(seed)
    idx = sorted(rng.sample(range(len(table)), max(1, int(len(table) * frac))))
    return Table(list(table.columns), [list(table.rows[i]) for i in idx])


def t_shuffle_columns(table: Table, seed: int = 0) -> Table:
    """列重排。"""
    rng = random.Random(seed)
    order = list(range(len(table.columns)))
    rng.shuffle(order)
    return Table([table.columns[j] for j in order], [[r[j] for j in order] for r in table.rows])


def t_concat_fields(table: Table, seed: int = 0) -> Table:
    """字段拼接：追加两个无关列（模拟与其他数据集拼接后转售）。"""
    rng = random.Random(seed)
    t = table.copy()
    t.columns = list(t.columns) + ["备注标签", "批次号"]
    for i, r in enumerate(t.rows):
        r.append(rng.choice(["A", "B", "C"]))
        r.append(f"B{i // 10:04d}")
    return t


def t_shuffle_rows(table: Table, seed: int = 0) -> Table:
    """行乱序。"""
    rng = random.Random(seed)
    rows = [list(r) for r in table.rows]
    rng.shuffle(rows)
    return Table(list(table.columns), rows)


TRANSFORMS = {
    "截断": lambda t, s=0: t_truncate(t, 0.4),
    "加噪": lambda t, s=0: t_noise(t, 0.0025, s),
    "抽样": lambda t, s=0: t_sample(t, 0.5, s),
    "列重排": lambda t, s=0: t_shuffle_columns(t, s),
    "字段拼接": lambda t, s=0: t_concat_fields(t, s),
    "行乱序": lambda t, s=0: t_shuffle_rows(t, s),
}


__all__ = [
    "Table",
    "RobustFingerprint",
    "numeric_columns",
    "TRANSFORMS",
    "t_truncate",
    "t_noise",
    "t_sample",
    "t_shuffle_columns",
    "t_concat_fields",
    "t_shuffle_rows",
]
