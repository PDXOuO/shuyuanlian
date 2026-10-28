"""合成数据集：模拟"多源公共数据"的真实形态（月度面板数据，36 家企业 × 12 个月）。

说明：本作品不接触任何真实公共数据。数据集为按真实统计口径合成的脱敏样例，
仅用于验证登记、授权、分账、溯源与隐私聚合的完整链路。

字段设计上保留"记录标识列"（PK 编号），这是公共数据元件的标准字段，
也是指纹定位（键控哈希决定码位）所依赖的稳定锚点。
"""

from __future__ import annotations

import random
from pathlib import Path

from .config import DATA_DIR
from .fingerprint import RobustFingerprint, Table

ENTERPRISES = [f"E{i:03d}" for i in range(1, 37)]
MONTHS = [f"2025-{m:02d}" for m in range(1, 13)]
INDUSTRIES = ["装备制造", "新材料", "电子信息", "生物医药", "汽车零部件", "纺织"]


def _profile(rng: random.Random) -> list[dict]:
    prof = []
    for i, eid in enumerate(ENTERPRISES):
        prof.append(
            {
                "id": eid,
                "scale": rng.uniform(0.4, 3.2),
                "cap": round(rng.uniform(0.4, 3.2) * rng.uniform(600, 900), 2),
                "industry": INDUSTRIES[i % len(INDUSTRIES)],
                "trend": rng.uniform(-0.02, 0.05),
            }
        )
    return prof


def build_park_table(seed: int = 20251028) -> Table:
    """某工业园区企业月度用电（原始元件）。"""
    rng = random.Random(seed)
    prof = _profile(rng)
    cols = ["记录编号", "企业编号", "月份", "用电量(kWh)", "变压器容量(kVA)", "月度电费(元)", "最大负荷(kW)"]
    rows = []
    pk = 0
    for p in prof:
        for m, month in enumerate(MONTHS):
            pk += 1
            season = 1.18 if m in (6, 7, 8, 11, 12) else (0.92 if m in (1, 2) else 1.0)
            kwh = p["scale"] * 42000 * season * (1 + p["trend"] * m) * rng.uniform(0.94, 1.06)
            price = 0.62 + rng.uniform(-0.04, 0.06)
            rows.append(
                [
                    f"PK{pk:05d}",
                    p["id"],
                    month,
                    round(kwh, 2),
                    round(p["cap"], 2),
                    round(kwh * price, 2),
                    round(kwh / 730 * rng.uniform(1.35, 1.75), 2),
                ]
            )
    return Table(cols, rows)


def build_stat_table(seed: int = 20251029) -> Table:
    """规模以上工业企业产值登记（原始元件）。"""
    rng = random.Random(seed)
    prof = _profile(rng)
    cols = ["记录编号", "企业编号", "月份", "工业总产值(万元)", "增加值增速(%)", "从业人员(人)"]
    rows = []
    pk = 0
    for p in prof:
        base = p["scale"] * rng.uniform(380, 620)
        for m, month in enumerate(MONTHS):
            pk += 1
            output = base * (1 + p["trend"] * m) * rng.uniform(0.95, 1.05)
            rows.append(
                [
                    f"PK{pk:05d}",
                    p["id"],
                    month,
                    round(output, 2),
                    round(p["trend"] * 100 + rng.gauss(0, 2.4), 2),
                    int(p["scale"] * rng.uniform(120, 260)),
                ]
            )
    return Table(cols, rows)


def build_derived_table(park: Table, stat: Table) -> Table:
    """产业链景气度指数（派生元件）：由用电与产值共同加工，用于验证血缘分账。"""
    ci_pk, ci_eid, ci_month, ci_kwh = 0, 1, 2, 3
    si_pk, si_out = 0, 3
    park_map = {(r[ci_eid], r[ci_month]): float(r[ci_kwh]) for r in park.rows}
    stat_map = {(r[ci_eid], r[ci_month]): float(r[si_out]) for r in stat.rows}
    keys = sorted(set(park_map) & set(stat_map))
    intensity = {k: park_map[k] / max(stat_map[k], 1.0) for k in keys}
    vals = sorted(intensity.values())
    lo, hi = vals[int(0.02 * len(vals))], vals[int(0.98 * len(vals))]

    cols = ["记录编号", "企业编号", "月份", "景气度指数", "用电强度(kWh/万元)", "产值增速(%)"]
    rows = []
    prev: dict[str, float] = {}
    for i, k in enumerate(keys, start=1):
        eid, month = k
        norm = (intensity[k] - lo) / max(hi - lo, 1e-9)
        prev_out = prev.get(eid, stat_map[k])
        growth = (stat_map[k] - prev_out) / max(prev_out, 1e-9) * 100
        prev[eid] = stat_map[k]
        index = 62 + 28 * (1 - norm) + max(-12.0, min(12.0, growth * 0.8))
        rows.append(
            [
                f"PK{i:05d}",
                eid,
                month,
                round(max(5.0, min(99.0, index)), 2),
                round(intensity[k], 2),
                round(growth, 2),
            ]
        )
    return Table(cols, rows)


def site_split(table: Table, sites: int = 3) -> list[Table]:
    """按企业把一份元件切分为多个"站点"视角，模拟三家机构各自持有部分数据。"""
    groups: list[list[str]] = [[] for _ in range(sites)]
    for i, eid in enumerate(ENTERPRISES):
        groups[i % sites].append(eid)
    out = []
    for g in groups:
        gs = set(g)
        out.append(Table(list(table.columns), [list(r) for r in table.rows if r[1] in gs]))
    return out


def materialize(fp: RobustFingerprint | None = None) -> dict:
    """生成并落盘全部数据集，返回表、指纹码与文件路径。"""
    fp = fp or RobustFingerprint()
    out_dir: Path = DATA_DIR / "datasets"
    out_dir.mkdir(parents=True, exist_ok=True)

    park = build_park_table()
    stat = build_stat_table()
    derived = build_derived_table(park, stat)

    code_park = fp.code_for("EL-PARK-ELEC")
    code_index = fp.code_for("EL-CREDIT-INDEX")
    park_fp = fp.embed(park, code_park)
    index_fp = fp.embed(derived, code_index)

    paths = {
        "park_raw": park.to_csv(out_dir / "园区企业月度用电_原始.csv"),
        "park": park_fp.to_csv(out_dir / "园区企业月度用电_指纹版.csv"),
        "stat": stat.to_csv(out_dir / "规上企业产值_原始.csv"),
        "derived_raw": derived.to_csv(out_dir / "产业链景气度_原始.csv"),
        "derived": index_fp.to_csv(out_dir / "产业链景气度_指纹版.csv"),
    }
    return {
        "tables": {"park": park_fp, "stat": stat, "derived": index_fp},
        "raw": {"park": park, "stat": stat, "derived": derived},
        "codes": {"EL-PARK-ELEC": code_park, "EL-CREDIT-INDEX": code_index},
        "paths": paths,
        "fp": fp,
    }


def site_totals(table: Table, sites: int = 3) -> list[dict]:
    """各站点本地汇总（本地明文不出域，仅用于同态加密演示）。"""
    out = []
    names = ["站点A · 某工业园区", "站点B · 某区统计局", "站点C · 某行业协会"]
    kwh_col = table.col("用电量(kWh)")
    for i, sub in enumerate(site_split(table, sites)):
        total = sum(float(r[kwh_col]) for r in sub.rows)
        n = len({r[1] for r in sub.rows})
        out.append(
            {
                "site": names[i] if i < len(names) else f"站点{i + 1}",
                "rows": len(sub.rows),
                "enterprises": n,
                "total_kwh": round(total, 2),
                "mean_kwh": round(total / max(n, 1), 2),
            }
        )
    return out


__all__ = [
    "ENTERPRISES",
    "MONTHS",
    "build_park_table",
    "build_stat_table",
    "build_derived_table",
    "site_split",
    "site_totals",
    "materialize",
]
