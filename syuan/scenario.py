"""六幕演示编排：从元件登记到泄漏冻结、违规拦截与同态聚合。

每一幕都产生真实交易、真实区块与真实指标，没有"预设好的假动画"。
"""

from __future__ import annotations

import time
from typing import Any

from . import config as C
from . import datasets
from .engine import ChainEngine
from .crypto_utils import sha256_hex
from .fingerprint import RobustFingerprint, t_concat_fields, t_noise, t_sample, t_shuffle_columns
from .privacy import laplace_mechanism, load_or_create_keypair, mean_sensitivity

PRICE_PARK = 12_500_000      # 12.50 元 / 次
PRICE_STAT = 9_800_000       # 9.80 元 / 次
PRICE_INDEX = 20_000_000     # 20.00 元 / 次


def yuan(micro: int | float) -> str:
    return f"¥{micro / C.MICRO:,.2f}"


class Demo:
    def __init__(self) -> None:
        self.reset()

    # ------------------------------------------------------------------ 生命周期
    def reset(self) -> None:
        self.fp = RobustFingerprint()
        self.data = datasets.materialize(self.fp)
        self.engine = ChainEngine()
        self.kp = None
        self.logs: dict[int, list[dict[str, Any]]] = {i: [] for i in range(1, 7)}
        self.done: set[int] = set()
        self.meta: dict[str, Any] = {
            "policy_index": None,
            "policy_park": None,
            "leak": None,
            "privacy": None,
            "started_at": time.time(),
        }

    def log(self, act: int, kind: str, text: str, **extra) -> None:
        self.logs[act].append({"kind": kind, "text": text, "ts": time.time(), **extra})

    # ------------------------------------------------------------------ 运行
    def run(self, act: int) -> dict[str, Any]:
        for i in range(1, act + 1):
            if i not in self.done:
                self.logs[i] = []
                self.engine.defer(True)          # 一幕内的交易打包进同一个区块
                try:
                    getattr(self, f"_act{i}")()
                finally:
                    self.engine.defer(False)
                    self.engine.flush()
                self.done.add(i)
        return {"act": act, "logs": self.logs[act], "state": self.engine.snapshot()}

    # ------------------------------------------------------------------ 第一幕
    def _act1(self) -> None:
        e = self.engine
        park = self.data["tables"]["park"]
        stat = self.data["tables"]["stat"]
        derived = self.data["tables"]["derived"]

        self.log(1, "info", "① 三个数据元件向链上登记：原始数据不出域，只提交内容哈希与字段模式哈希。")
        r1 = e.invoke_as(
            "park", C.ADDR_REGISTRY, "registerElement",
            {
                "element_id": "EL-PARK-ELEC",
                "name": "园区企业月度用电",
                "content_hash": park.content_hash(),
                "schema_hash": park.schema_hash(),
                "parents": [],
                "price_micro": PRICE_PARK,
                "category": "原始数据",
                "rows": len(park),
                "quantum": C.FINGERPRINT_QUANTUM,
                "note": "36 家企业 × 12 个月，含电量/容量/电费/负荷四项",
            },
        )
        self.log(1, "tx", f"登记 EL-PARK-ELEC：{len(park)} 行，内容哈希 {park.content_hash()[:16]}…", tx=r1)

        r2 = e.invoke_as(
            "stat", C.ADDR_REGISTRY, "registerElement",
            {
                "element_id": "EL-STAT-OUTPUT",
                "name": "规上企业产值登记",
                "content_hash": stat.content_hash(),
                "schema_hash": stat.schema_hash(),
                "parents": [],
                "price_micro": PRICE_STAT,
                "category": "原始数据",
                "rows": len(stat),
                "note": "产值、增加值增速、从业人员",
            },
        )
        self.log(1, "tx", f"登记 EL-STAT-OUTPUT：{len(stat)} 行，内容哈希 {stat.content_hash()[:16]}…", tx=r2)

        r3 = e.invoke_as(
            "processor", C.ADDR_REGISTRY, "registerElement",
            {
                "element_id": "EL-CREDIT-INDEX",
                "name": "产业链景气度指数",
                "content_hash": derived.content_hash(),
                "schema_hash": derived.schema_hash(),
                "parents": [{"id": "EL-PARK-ELEC", "weight": 3}, {"id": "EL-STAT-OUTPUT", "weight": 2}],
                "price_micro": PRICE_INDEX,
                "self_share_bp": 6000,
                "category": "派生数据",
                "rows": len(derived),
                "note": "由用电强度与产值增速加工合成",
            },
        )
        self.log(1, "tx", "登记派生元件 EL-CREDIT-INDEX：血缘父节点 [EL-PARK-ELEC ×3, EL-STAT-OUTPUT ×2]", tx=r3)

        f1 = e.invoke_as(
            "park", C.ADDR_TRACE, "registerFingerprint",
            {
                "element_id": "EL-PARK-ELEC",
                "code_hex": RobustFingerprint.bits_to_hex(self.data["codes"]["EL-PARK-ELEC"]),
                "quantum": C.FINGERPRINT_QUANTUM,
                "bits": C.FINGERPRINT_BITS,
                "rows": len(park),
                "columns": park.columns,
            },
        )
        self.log(1, "tx", f"登记鲁棒指纹：32 bit，量化步长 {C.FINGERPRINT_QUANTUM}，分布于全部数值列", tx=f1)

        f2 = e.invoke_as(
            "processor", C.ADDR_TRACE, "registerFingerprint",
            {
                "element_id": "EL-CREDIT-INDEX",
                "code_hex": RobustFingerprint.bits_to_hex(self.data["codes"]["EL-CREDIT-INDEX"]),
                "quantum": C.FINGERPRINT_QUANTUM,
                "bits": C.FINGERPRINT_BITS,
                "rows": len(derived),
                "columns": derived.columns,
            },
        )
        self.log(1, "tx", "派生元件同样嵌入独立指纹（每个元件一码，可定位到具体源头）", tx=f2)
        self.log(1, "ok", f"链上已登记 3 个元件、2 组指纹，区块高度 {e.chain.height}")

    # ------------------------------------------------------------------ 第二幕
    def _act2(self) -> None:
        e = self.engine
        self.log(2, "info", "② 银行申请派生元件授权：目的、次数、有效期、单价一次写进合约。")
        r = e.invoke_as(
            "bank", C.ADDR_ACCESS, "grant",
            {
                "element_id": "EL-CREDIT-INDEX",
                "consumer": e.addr("bank"),
                "purpose": "绿色信贷风控建模",
                "max_calls": 100,
                "ttl_seconds": 7 * 86400,
                "price_micro": PRICE_INDEX,
            },
        )
        self.log(2, "reject", "反例：银行自己签发授权 —— 被合约拒绝（只有持有者能签发）", tx=r)

        g = e.invoke_as(
            "processor", C.ADDR_ACCESS, "grant",
            {
                "element_id": "EL-CREDIT-INDEX",
                "consumer": e.addr("bank"),
                "purpose": "绿色信贷风控建模",
                "max_calls": 100,
                "ttl_seconds": 7 * 86400,
                "price_micro": PRICE_INDEX,
            },
        )
        pid = g["result"]["policy_id"]
        self.meta["policy_index"] = pid
        self.log(2, "tx", f"加工方签发授权 {pid}：用途「绿色信贷风控建模」，100 次，7 天有效", tx=g)
        self.log(2, "ok", f"授权已上链，状态 {g['result']['state']}，剩余 {g['result']['remaining']} 次")

    # ------------------------------------------------------------------ 第三幕
    def _act3(self) -> None:
        e = self.engine
        pid = self.meta["policy_index"]
        self.log(3, "info", "③ 银行连续调用 3 次：每次调用触发链上扣次与沿血缘的自动分账。")
        for i in range(1, 4):
            r = e.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "绿色信贷风控建模"})
            parts = (r["result"] or {}).get("settlement", {}).get("parts", [])
            detail = "，".join(f"{p['name']} {yuan(p['amount_micro'])}" for p in parts)
            self.log(
                3, "tx",
                f"第 {i} 次调用 → 收入 {yuan(PRICE_INDEX)} 拆分为：{detail}",
                tx=r,
                parts=parts,
            )
        bals = e.balances()
        self.log(3, "ok", "分账余额：" + "，".join(f"{b['name']} {yuan(b['balance_micro'])}" for b in bals))
        self.log(3, "info", "注意：加工方只留 60%，其余 40% 按 3:2 递归分给两个上游原始数据持有方 —— 上游不再是一次性卖断。")

    # ------------------------------------------------------------------ 第四幕
    def _act4(self) -> None:
        e = self.engine
        derived = self.data["tables"]["derived"]
        self.log(4, "info", "④ 第三方渠道出现疑似转售样本：经过抽样、加噪、列重排、字段拼接后，仍要能追溯。")
        sample = t_sample(derived, 0.45, seed=42)
        sample = t_noise(sample, 0.0025, seed=7)
        sample = t_shuffle_columns(sample, seed=11)
        sample = t_concat_fields(sample, seed=13)
        path = self.data["paths"]["derived"].parent / "转售样本_第三方渠道.csv"
        sample.to_csv(path)
        sample_hash = sample.content_hash()
        self.log(4, "info", f"样本已获取：{len(sample)} 行（原表 {len(derived)} 行），证据哈希 {sample_hash[:16]}…")

        code = self.data["codes"]["EL-CREDIT-INDEX"]
        det = self.fp.detect(sample, code)
        self.log(
            4, "privacy",
            f"指纹检测：命中 {det['matched']}/{det['bits']} bit，相似度 {det['similarity_bp'] / 100:.1f}%，"
            f"定位主键列「{det['id_column']}」，判定门限 {C.TRACE_HIT_BP / 100:.1f}%",
            data=det,
        )

        r = e.invoke_as(
            "oracle", C.ADDR_TRACE, "reportLeak",
            {
                "element_id": "EL-CREDIT-INDEX",
                "similarity_bp": det["similarity_bp"],
                "evidence_hash": sample_hash,
                "sample_source": "某第三方数据交易渠道在售样本",
                "operator": "第三方数据溯源机构",
                "note": "样本字段与指纹版元件一致，判定为未授权转售",
            },
        )
        case = r["result"]
        self.meta["leak"] = {"case": case, "detection": det, "sample_path": str(path)}
        self.log(
            4, "tx",
            f"链上立案 {case['case_id']}：自动冻结元件 EL-CREDIT-INDEX 及 {case['frozen_policies']} 条在效授权",
            tx=r,
        )
        self.log(
            4, "warn",
            "排查清单（链上已知曾获授权的主体）："
            + "、".join(f"{c['consumer_name']}（调用 {c['used']} 次）" for c in case["consumers"]),
        )

        pid = self.meta["policy_index"]
        r2 = e.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "绿色信贷风控建模"})
        self.log(4, "reject", f"冻结后银行再次调用 → 合约拒绝（{r2['code']}：{r2['message']}）", tx=r2)

    # ------------------------------------------------------------------ 第五幕
    def _act5(self) -> None:
        e = self.engine
        self.log(5, "info", "⑤ 授权策略的三道闸门：用途白名单、次数上限、调用者身份。")
        g = e.invoke_as(
            "park", C.ADDR_ACCESS, "grant",
            {
                "element_id": "EL-PARK-ELEC",
                "consumer": e.addr("bank"),
                "purpose": "区域经济运行分析",
                "max_calls": 3,
                "ttl_seconds": 86400,
                "price_micro": PRICE_PARK,
            },
        )
        pid = g["result"]["policy_id"]
        self.meta["policy_park"] = pid

        r = e.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "广告投放人群定向"})
        self.log(5, "reject", f"闸门一 · 用途不符 → 拒绝（{r['code']}：{r['message']}）", tx=r)

        for i in range(1, 4):
            r = e.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "区域经济运行分析"})
            self.log(5, "tx", f"合规调用 {i}/3 → 剩余 {r['result']['policy']['remaining']} 次", tx=r)

        r = e.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "区域经济运行分析"})
        self.log(5, "reject", f"闸门二 · 次数用尽 → 拒绝（{r['code']}：{r['message']}）", tx=r)

        r = e.invoke_as("reseller", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "区域经济运行分析"})
        self.log(5, "reject", f"闸门三 · 非授权主体冒用 → 拒绝（{r['code']}：{r['message']}）", tx=r)

        r = e.invoke_as("bank", C.ADDR_SETTLEMENT, "settle",
                        {"element_id": "EL-PARK-ELEC", "amount_micro": 100_000_000, "policy_id": pid})
        self.log(5, "reject", f"闸门四 · 绕过授权合约直接调结算 → 拒绝（{r['code']}：{r['message']}）", tx=r)

    # ------------------------------------------------------------------ 第六幕
    def _act6(self) -> None:
        e = self.engine
        self.log(6, "info", "⑥ 三家机构各自持有部分用电数据：密文求和后只解出行业均值，任何一方看不到他方明文。")
        if self.kp is None:
            self.kp = load_or_create_keypair(C.PAILLIER_KEY_FILE, 1024)
        park = self.data["tables"]["park"]
        sites = datasets.site_totals(park, 3)

        ciphertexts = []
        cs = []
        for s in sites:
            m = int(round(s["total_kwh"] * 100))  # 以 0.01 kWh 为最小单位
            c = self.kp.pub.encrypt(m)
            cs.append(c)
            ciphertexts.append(
                {
                    "site": s["site"],
                    "rows": s["rows"],
                    "ciphertext": str(c)[:72] + "…",
                    "ct_bits": self.kp.n.bit_length(),
                    "plain_local": s["total_kwh"],
                }
            )
            self.log(6, "privacy", f"{s['site']} 本地加密：{s['total_kwh']:,.2f} kWh → 密文 {str(c)[:28]}…（明文不出域）")

        acc = cs[0]
        for c in cs[1:]:
            acc = self.kp.pub.add(acc, c)

        total = self.kp.decrypt(acc) / 100.0
        n_ent = sum(s["enterprises"] for s in sites)
        true_mean = total / n_ent
        eps = 1.0
        sens = mean_sensitivity(150_000.0, n_ent)
        noisy_mean = laplace_mechanism(true_mean, sens, eps)

        self.log(6, "privacy", f"链下密文累加 → 解密得到行业总用电 {total:,.2f} kWh（仅释放聚合值）")
        self.log(6, "info", f"差分隐私发布：ε={eps}，敏感度 {sens:,.1f} kWh，加噪后均值 {noisy_mean:,.2f} kWh")
        self.log(
            6, "ok",
            f"每家企业月均用电 {true_mean:,.2f} kWh，发布值 {noisy_mean:,.2f} kWh，"
            f"相对误差 {abs(noisy_mean - true_mean) / true_mean * 100:.2f}%",
        )
        result_hash = sha256_hex(f"{total:.2f}|{noisy_mean:.2f}|{eps}|{n_ent}")
        anchor = e.invoke_as(
            "admin", C.ADDR_ANCHOR, "anchor",
            {
                "round_id": f"AGG-{time.strftime('%Y%m%d')}-01",
                "result_hash": result_hash,
                "party_count": len(sites),
                "epsilon": eps,
                "scheme": "Paillier-1024 + Laplace",
                "params": {"bound_kwh": 150000.0, "n_enterprises": n_ent, "granularity": 0.01},
                "note": "密文求和后仅发布行业均值，参与方与参数上链锚定",
            },
        )
        self.log(6, "tx", f"聚合结果哈希 {result_hash[:16]}… 已上链锚定（{anchor['result']['round_id']}），"
                          "链上不存任何明文", tx=anchor)

        self.meta["privacy"] = {
            "sites": sites,
            "ciphertexts": ciphertexts,
            "total_kwh": total,
            "n_enterprises": n_ent,
            "true_mean": true_mean,
            "noisy_mean": noisy_mean,
            "epsilon": eps,
            "sensitivity": sens,
            "key_bits": self.kp.n.bit_length(),
            "ct_len": len(str(cs[0])),
        }

    # ------------------------------------------------------------------ 便捷
    def run_all(self) -> dict[str, Any]:
        self.run(6)
        return self.engine.snapshot()

    def state(self) -> dict[str, Any]:
        snap = self.engine.snapshot()
        snap["acts"] = sorted(self.done)
        snap["logs"] = {k: v for k, v in self.logs.items() if v}
        snap["meta"] = {
            "policy_index": self.meta["policy_index"],
            "policy_park": self.meta["policy_park"],
            "leak": {
                "case_id": (self.meta["leak"] or {}).get("case", {}).get("case_id"),
                "similarity_bp": (self.meta["leak"] or {}).get("detection", {}).get("similarity_bp"),
            },
            "privacy": self.meta["privacy"],
        }
        return snap
