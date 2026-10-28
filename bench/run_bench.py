"""基准与安全实验：所有写进测试报告的数字都由本脚本实测产生。

    python main.py bench            # 或 python bench/run_bench.py

输出：
    results/metrics.json            # 全部指标
    results/charts/*.png            # 报告用图
"""

from __future__ import annotations

import json
import platform
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from syuan import config as C
from syuan import datasets
from syuan.crypto_utils import sha256_hex
from syuan.engine import ChainEngine
from syuan.fingerprint import RobustFingerprint, TRANSFORMS, t_noise
from syuan.privacy import laplace_noise, load_or_create_keypair, mean_sensitivity
from syuan.scenario import Demo, PRICE_INDEX, yuan

RESULTS = C.RESULTS_DIR
CHARTS = RESULTS / "charts"
CHARTS.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------- 工具
def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1)))))
    return s[k]


def setup_engine(max_calls: int = 1200) -> tuple[ChainEngine, str]:
    """构造三级血缘（原始→加工→再加工），用于压测跨层级联分账。"""
    e = ChainEngine()
    e.invoke_as("park", C.ADDR_REGISTRY, "registerElement",
                {"element_id": "EL-ROOT", "name": "原始数据集", "content_hash": "a" * 64,
                 "schema_hash": "b" * 64, "price_micro": 4_000_000, "rows": 432})
    e.invoke_as("processor", C.ADDR_REGISTRY, "registerElement",
                {"element_id": "EL-MID", "name": "一级加工元件", "content_hash": "c" * 64,
                 "schema_hash": "d" * 64, "parents": [{"id": "EL-ROOT", "weight": 2}],
                 "price_micro": 10_000_000, "self_share_bp": 6000, "rows": 432})
    e.invoke_as("stat", C.ADDR_REGISTRY, "registerElement",
                {"element_id": "EL-TOP", "name": "二级加工元件", "content_hash": "e" * 64,
                 "schema_hash": "f" * 64, "parents": [{"id": "EL-MID", "weight": 1}],
                 "price_micro": PRICE_INDEX, "self_share_bp": 6000, "rows": 432})
    g = e.invoke_as(
        "stat", C.ADDR_ACCESS, "grant",
        {
            "element_id": "EL-TOP",
            "consumer": e.addr("bank"),
            "purpose": "压测用途",
            "max_calls": max_calls,
            "ttl_seconds": 365 * 86400,
            "price_micro": PRICE_INDEX,
        },
    )
    if g["status"] != "SUCCESS":
        raise RuntimeError(f"压测环境初始化失败：{g['code']} {g['message']}")
    return e, g["result"]["policy_id"]


# --------------------------------------------------------------------- 1. 吞吐
def bench_throughput(n: int = 1000, batch: int = 200) -> dict:
    e, pid = setup_engine(max_calls=n + 100)
    lat: list[float] = []
    t0 = time.perf_counter()
    for start in range(0, n, batch):
        e.defer(True)
        for _ in range(min(batch, n - start)):
            a = time.perf_counter()
            e.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "压测用途"}, commit=False)
            lat.append((time.perf_counter() - a) * 1000)
        e.defer(False)
        e.flush()
    elapsed = time.perf_counter() - t0

    # 只读调用 QPS
    t1 = time.perf_counter()
    for _ in range(2000):
        e.call(C.ADDR_ACCESS, "check", {"policy_id": pid, "purpose": "压测用途"})
    read_elapsed = time.perf_counter() - t1

    # 交易签名与验签单项耗时
    from syuan.crypto_utils import Wallet

    w = Wallet.generate()
    t2 = time.perf_counter()
    for _ in range(300):
        w.sign(b"benchmark-payload")
    sign_ms = (time.perf_counter() - t2) / 300 * 1000
    return {
        "txs": n,
        "batch_size": batch,
        "elapsed_s": round(elapsed, 3),
        "tps": round(n / elapsed, 1),
        "latency_ms": {
            "mean": round(statistics.mean(lat), 3),
            "p50": round(pct(lat, 50), 3),
            "p95": round(pct(lat, 95), 3),
            "p99": round(pct(lat, 99), 3),
            "max": round(max(lat), 3),
        },
        "read_qps": round(2000 / read_elapsed, 1),
        "sign_ms_per_tx": round(sign_ms, 3),
        "blocks": e.chain.height,
        "avg_block_ms": e.stats()["avg_block_ms"],
        "gas_per_tx": round(e.gas_total / max(n, 1)),
        "state_bytes": len(json.dumps(e.state.data, ensure_ascii=False).encode()),
    }


# --------------------------------------------------------------------- 2. 分账守恒
def bench_settlement(n: int = 1000) -> dict:
    e, pid = setup_engine(max_calls=n + 100)
    e.defer(True)
    for _ in range(n):
        e.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "压测用途"}, commit=False)
    e.defer(False)
    e.flush()

    settled = [ev for ev in e.events if ev["topic"] == "Settled"]
    diffs = []
    for ev in settled:
        parts = ev["payload"]["parts"]
        diffs.append(sum(p["amount_micro"] for p in parts) - ev["payload"]["amount_micro"])
    balances = e.balances()
    total_balance = sum(b["balance_micro"] for b in balances)
    revenue = n * PRICE_INDEX
    # 分账层级分布
    hop_max = max((max(p["hop"] for p in ev["payload"]["parts"]) for ev in settled), default=0)
    return {
        "calls": n,
        "settled_events": len(settled),
        "max_abs_diff_micro": max(abs(d) for d in diffs) if diffs else 0,
        "nonzero_diff_count": sum(1 for d in diffs if d != 0),
        "total_revenue_micro": revenue,
        "total_balance_micro": total_balance,
        "conservation_error_micro": total_balance - revenue,
        "conservation_error_ratio": 0.0 if total_balance == revenue else (total_balance - revenue) / revenue,
        "max_hop": hop_max,
        "parties_paid": len(balances),
        "balances": balances,
        "settle_stats": e.settlement_stats(),
    }


# --------------------------------------------------------------------- 3. 策略闸门
def bench_policy_guard(trials: int = 20) -> dict:
    e_ok, pid_ok = setup_engine(max_calls=100_000)   # 合规用例使用足额额度
    e, pid = setup_engine(max_calls=5)               # 违规用例使用 5 次额度
    cases = []

    def run(label: str, fn, expect_code: str | None):
        ok = 0
        codes = set()
        for _ in range(trials):
            r = fn()
            codes.add(r["code"] or r["status"])
            if (expect_code is None and r["status"] == "SUCCESS") or (
                expect_code and r["status"] == "REVERTED" and r["code"] == expect_code
            ):
                ok += 1
        cases.append(
            {"case": label, "expected": expect_code or "SUCCESS", "hit": ok, "trials": trials,
             "rate": round(ok / trials * 100, 1), "codes": sorted(c for c in codes if c)}
        )

    # 合规调用：先跑满 5 次内的正常调用
    run("合规调用（额度充足）", lambda: e_ok.invoke_as("bank", C.ADDR_ACCESS, "consume",
                                                   {"policy_id": pid_ok, "purpose": "压测用途"}), None)
    # 用途越权
    run("用途越权（非白名单目的）", lambda: e.invoke_as("bank", C.ADDR_ACCESS, "consume",
                                                  {"policy_id": pid, "purpose": "广告投放"}), "E_PURPOSE_MISMATCH")
    for _ in range(5):  # 把 5 次额度用尽
        e.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "压测用途"})
    # 次数耗尽
    run("次数耗尽后继续调用", lambda: e.invoke_as("bank", C.ADDR_ACCESS, "consume",
                                            {"policy_id": pid, "purpose": "压测用途"}), "E_EXHAUSTED")
    # 主体冒用
    run("非授权主体冒用他人凭证", lambda: e.invoke_as("reseller", C.ADDR_ACCESS, "consume",
                                               {"policy_id": pid, "purpose": "压测用途"}), "E_CONSUMER")
    # 非持有者签发授权
    run("非持有者伪造授权", lambda: e.invoke_as("bank", C.ADDR_ACCESS, "grant",
                                          {"element_id": "EL-TOP", "consumer": e.addr("bank"),
                                           "purpose": "压测用途", "max_calls": 1, "ttl_seconds": 3600}), "E_OWNER")
    # 绕过授权直接结算
    run("绕过授权合约直接结算", lambda: e.invoke_as("bank", C.ADDR_SETTLEMENT, "settle",
                                             {"element_id": "EL-TOP", "amount_micro": 1000, "policy_id": pid}), "E_AUTH")
    # 撤销后调用
    e2, pid2 = setup_engine(max_calls=10)
    rv = e2.invoke_as("stat", C.ADDR_ACCESS, "revoke", {"policy_id": pid2, "reason": "持有人主动撤回"})
    assert rv["status"] == "SUCCESS", rv
    ok = 0
    for _ in range(trials):
        r = e2.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid2, "purpose": "压测用途"})
        ok += int(r["status"] == "REVERTED" and r["code"] == "E_REVOKED")
    cases.append({"case": "授权撤销后继续调用", "expected": "E_REVOKED", "hit": ok, "trials": trials,
                  "rate": round(ok / trials * 100, 1), "codes": ["E_REVOKED"]})

    total_hit = sum(c["hit"] for c in cases)
    total_trials = sum(c["trials"] for c in cases)
    return {
        "cases": cases,
        "interception_rate": round(total_hit / total_trials * 100, 2),
        "violations_tested": total_trials,
    }


# --------------------------------------------------------------------- 4. 指纹
def bench_fingerprint(trials: int = 30, fpr_trials: int = 200) -> dict:
    fp = RobustFingerprint()
    data = datasets.materialize(fp)
    table = data["tables"]["derived"]
    bits = fp.bits

    rng = random.Random(20251030)
    per_transform = []
    for name, fn in TRANSFORMS.items():
        sims = []
        for k in range(trials):
            code = [rng.randint(0, 1) for _ in range(bits)]
            t = fp.embed(table, code)
            t2 = fn(t, k)
            sims.append(fp.detect(t2, code)["similarity_bp"] / 10000)
        hits = sum(1 for s in sims if s >= C.TRACE_HIT_BP / 10000)
        per_transform.append(
            {
                "transform": name,
                "trials": trials,
                "mean_similarity": round(statistics.mean(sims) * 100, 2),
                "min_similarity": round(min(sims) * 100, 2),
                "detection_rate": round(hits / trials * 100, 1),
            }
        )

    # 噪声强度扫描（以量化步长为单位）
    noise_curve = []
    for ratio in (0.05, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1.0):
        sigma = C.FINGERPRINT_QUANTUM * ratio
        sims = []
        for k in range(max(12, trials // 2)):
            code = [rng.randint(0, 1) for _ in range(bits)]
            t = fp.embed(table, code)
            sims.append(fp.detect(t_noise(t, sigma, k), code)["similarity_bp"] / 10000)
        hits = sum(1 for s in sims if s >= C.TRACE_HIT_BP / 10000)
        noise_curve.append(
            {"sigma_ratio": ratio, "sigma": round(sigma, 5),
             "mean_similarity": round(statistics.mean(sims) * 100, 2),
             "detection_rate": round(hits / len(sims) * 100, 1)}
        )

    # 误报率：随机码与已嵌入样本比对
    fp_false = 0
    for _ in range(fpr_trials):
        other = [rng.randint(0, 1) for _ in range(bits)]
        if fp.detect(table, other)["similarity_bp"] >= C.TRACE_HIT_BP:
            fp_false += 1

    # 主键被重编号后的检出率（边界条件，如实报告）
    code = data["codes"]["EL-CREDIT-INDEX"]
    renum = table.copy()
    for i, r in enumerate(renum.rows, start=1):
        r[0] = f"NEW{i:05d}"
    det_renum = fp.detect(renum, code)

    # 嵌入代价
    orig = data["raw"]["derived"]
    emb = table
    diffs = []
    for j in range(1, len(orig.columns)):
        try:
            for a, b in zip(orig.rows, emb.rows):
                diffs.append(abs(float(b[j]) - float(a[j])))
        except (TypeError, ValueError):
            continue
    return {
        "bits": bits,
        "quantum": C.FINGERPRINT_QUANTUM,
        "rows": len(table),
        "carriers": len([c for c in table.columns if c not in ("记录编号", "企业编号", "月份")]),
        "votes_per_bit": round(len(table) * 4 / bits, 1),
        "per_transform": per_transform,
        "noise_curve": noise_curve,
        "fpr_trials": fpr_trials,
        "false_positive": fp_false,
        "fpr": round(fp_false / fpr_trials * 100, 2),
        "renumber_similarity": round(det_renum["similarity_bp"] / 100, 1),
        "max_value_distortion": round(max(diffs), 6) if diffs else 0,
        "mean_value_distortion": round(statistics.mean(diffs), 6) if diffs else 0,
    }


# --------------------------------------------------------------------- 5. 同态与差分隐私
def bench_privacy() -> dict:
    kp = load_or_create_keypair(C.PAILLIER_KEY_FILE, 1024)
    scales: list[dict] = []
    for parties in (3, 10, 30):
        vals = [random.randint(10_000_000, 90_000_000) for _ in range(parties)]
        t0 = time.perf_counter()
        cts = [kp.pub.encrypt(v) for v in vals]
        t_enc = (time.perf_counter() - t0) / parties * 1000
        t0 = time.perf_counter()
        acc = cts[0]
        for c in cts[1:]:
            acc = kp.pub.add(acc, c)
        t_add = (time.perf_counter() - t0) / max(parties - 1, 1) * 1000
        t0 = time.perf_counter()
        plain = kp.decrypt(acc)
        t_dec = (time.perf_counter() - t0) * 1000
        assert plain == sum(vals), "同态加法结果错误"
        scales.append(
            {
                "parties": parties,
                "encrypt_ms": round(t_enc, 3),
                "add_ms": round(t_add, 4),
                "decrypt_ms": round(t_dec, 3),
                "ct_chars": len(str(cts[0])),
                "error": 0,
            }
        )

    # 差分隐私：ε 与相对误差
    bound, n_ent, true_mean = 150_000.0, 36, 1_101_651.77
    dp_curve = []
    for eps in (0.1, 0.2, 0.5, 1.0, 2.0, 5.0):
        sens = mean_sensitivity(bound, n_ent)
        errs = [abs(laplace_noise(sens / eps)) / true_mean * 100 for _ in range(3000)]
        dp_curve.append(
            {
                "epsilon": eps,
                "sensitivity": round(sens, 1),
                "mean_rel_err_pct": round(statistics.mean(errs), 3),
                "p95_rel_err_pct": round(pct(errs, 95), 3),
            }
        )
    return {
        "key_bits": kp.n.bit_length(),
        "he": scales,
        "dp": {"bound": bound, "n": n_ent, "curve": dp_curve},
    }


# --------------------------------------------------------------------- 6. 共识与可验证性
def bench_consensus() -> dict:
    e = ChainEngine()
    e.invoke_as("park", C.ADDR_REGISTRY, "registerElement",
                {"element_id": "EL-C", "name": "共识测试", "content_hash": "c" * 64, "schema_hash": "d" * 64,
                 "price_micro": 1_000_000})
    base = e.chain.height

    # 1 节点离线
    e.chain.validators[3].online = False
    ok_one = 0
    for i in range(5):
        r = e.invoke_as("park", C.ADDR_REGISTRY, "setPrice", {"element_id": "EL-C", "price_micro": 1_000_000 + i})
        ok_one += int(r["status"] == "SUCCESS")
    height_after_one = e.chain.height

    # 2 节点离线
    e.chain.validators[2].online = False
    halted = False
    try:
        e.invoke_as("park", C.ADDR_REGISTRY, "setPrice", {"element_id": "EL-C", "price_micro": 9})
    except Exception as exc:  # noqa: BLE001
        halted = "门限" in str(exc)

    # 恢复并追块
    e.chain.validators[2].online = True
    e.chain.validators[3].online = True
    synced = e.chain.sync_node("validator-4")
    e.invoke_as("park", C.ADDR_REGISTRY, "setPrice", {"element_id": "EL-C", "price_micro": 1_000_001})

    # 篡改检测
    import copy as _copy

    blocks = e.chain.validators[0].blocks
    tampered = _copy.deepcopy(blocks[-1])
    tampered["txs"][0]["args"]["price_micro"] = 1
    det_block = not e.chain.verify_block(tampered, blocks[-2])[0]

    tampered2 = _copy.deepcopy(blocks[-1])
    tampered2["state_root"] = "f" * 64
    det_state = not e.chain.verify_block(tampered2, blocks[-2])[0]

    # 少签名
    tampered3 = _copy.deepcopy(blocks[-1])
    tampered3["sigs"] = dict(list(tampered3["sigs"].items())[:2])
    det_sig = not e.chain.verify_block(tampered3, blocks[-2])[0]

    rep = e.replay()
    proof = e.chain.inclusion_proof(1, 0)
    return {
        "validators": len(e.chain.validators),
        "quorum": e.chain.quorum,
        "blocks_with_1_down": height_after_one - base,
        "success_with_1_down": ok_one,
        "halt_with_2_down": halted,
        "synced_blocks_on_recover": synced,
        "tamper_detected_tx": det_block,
        "tamper_detected_state_root": det_state,
        "tamper_detected_missing_sig": det_sig,
        "replay_ok": rep["ok"],
        "replay_blocks": rep["blocks"],
        "replay_txs": rep["txs"],
        "merkle_proof_valid": proof["valid"],
        "block_sigs": len(blocks[-1]["sigs"]),
    }


# --------------------------------------------------------------------- 7. 全流程端到端
def bench_e2e() -> dict:
    t0 = time.perf_counter()
    demo = Demo()
    t_setup = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    demo.run(6)
    t_acts = time.perf_counter() - t0
    eng = demo.engine
    return {
        "setup_ms": round(t_setup, 1),
        "six_acts_s": round(t_acts, 2),
        "blocks": eng.chain.height,
        "txs": eng.chain.tx_count(),
        "integrity": eng.chain.integrity(),
        "balances": eng.balances(),
        "settlement": eng.settlement_stats(),
        "detection": demo.meta["leak"]["detection"]["similarity_bp"] if demo.meta["leak"] else None,
        "cases": len(eng.cases()),
        "privacy": {k: v for k, v in (demo.meta["privacy"] or {}).items()
                    if k in ("total_kwh", "true_mean", "noisy_mean", "epsilon", "key_bits")},
    }


# --------------------------------------------------------------------- 图表
def make_charts(m: dict) -> list[str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DengXian"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 160
    plt.rcParams["axes.edgecolor"] = "#d7dfea"
    plt.rcParams["axes.labelcolor"] = "#334155"
    plt.rcParams["xtick.color"] = "#475569"
    plt.rcParams["ytick.color"] = "#475569"
    plt.rcParams["text.color"] = "#0f172a"

    files = []
    accent, ok, warn, purple = "#1d4ed8", "#0f8f6a", "#b45309", "#6d28d9"

    # 图 1：指纹检出率
    fp = m["fingerprint"]
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.4))
    names = [x["transform"] for x in fp["per_transform"]]
    rates = [x["detection_rate"] for x in fp["per_transform"]]
    axes[0].bar(names, rates, color=accent, width=0.62)
    axes[0].set_ylim(0, 108)
    axes[0].set_ylabel("检出率（%）")
    axes[0].set_title("六种变换下的指纹检出率", fontsize=10.5)
    for i, v in enumerate(rates):
        axes[0].text(i, v + 2, f"{v:.0f}", ha="center", fontsize=8, color="#334155")
    axes[0].tick_params(axis="x", labelsize=8.5, rotation=18)

    x = [c["sigma_ratio"] for c in fp["noise_curve"]]
    y = [c["detection_rate"] for c in fp["noise_curve"]]
    s = [c["mean_similarity"] for c in fp["noise_curve"]]
    axes[1].plot(x, y, "o-", color=ok, label="检出率", linewidth=1.8, markersize=4)
    axes[1].plot(x, s, "s--", color=warn, label="平均相似度", linewidth=1.3, markersize=3.4)
    axes[1].axhline(85, color="#c0392b", linestyle=":", linewidth=1.1, label="判定门限 85%")
    axes[1].set_xlabel("噪声强度 σ / 量化步长 Δ")
    axes[1].set_ylabel("%")
    axes[1].set_ylim(40, 108)
    axes[1].set_title("噪声强度对溯源的影响", fontsize=10.5)
    axes[1].legend(fontsize=8, frameon=False)
    fig.tight_layout()
    p = CHARTS / "chart_fingerprint.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    files.append(str(p))

    # 图 2：吞吐与延迟
    tp = m["throughput"]
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.2))
    labels = ["P50", "P95", "P99", "Max"]
    vals = [tp["latency_ms"]["p50"], tp["latency_ms"]["p95"], tp["latency_ms"]["p99"], tp["latency_ms"]["max"]]
    axes[0].bar(labels, vals, color=[accent, accent, warn, "#c0392b"], width=0.55)
    for i, v in enumerate(vals):
        axes[0].text(i, v * 1.02, f"{v:.2f}", ha="center", fontsize=8, color="#334155")
    axes[0].set_ylabel("单笔交易端到端时延（ms）")
    axes[0].set_title(f"合约调用时延分布（{tp['txs']} 笔实测）", fontsize=10.5)

    names = ["交易吞吐\n(TPS)", "只读调用\n(QPS/10)"]
    vals2 = [tp["tps"], tp["read_qps"] / 10]
    axes[1].bar(names, vals2, color=[ok, purple], width=0.5)
    for i, v in enumerate(vals2):
        txt = f"{tp['tps']:.0f}" if i == 0 else f"{tp['read_qps']:.0f}"
        axes[1].text(i, v * 1.02, txt, ha="center", fontsize=9, color="#334155")
    axes[1].set_title("吞吐量（单机 4 节点同进程）", fontsize=10.5)
    fig.tight_layout()
    p = CHARTS / "chart_perf.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    files.append(str(p))

    # 图 3：差分隐私 ε 曲线
    dp = m["privacy"]["dp"]["curve"]
    fig, ax = plt.subplots(figsize=(5.6, 3.1))
    x = [d["epsilon"] for d in dp]
    y1 = [d["mean_rel_err_pct"] for d in dp]
    y2 = [d["p95_rel_err_pct"] for d in dp]
    ax.plot(x, y1, "o-", color=accent, label="平均相对误差", linewidth=1.8, markersize=4)
    ax.plot(x, y2, "s--", color=warn, label="95 分位相对误差", linewidth=1.3, markersize=3.4)
    ax.set_xscale("log")
    ax.set_xlabel("隐私预算 ε（对数轴）")
    ax.set_ylabel("相对误差（%）")
    ax.set_title("差分隐私：隐私预算与发布精度", fontsize=10.5)
    ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.25, linestyle=":")
    fig.tight_layout()
    p = CHARTS / "chart_dp.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    files.append(str(p))

    # 图 4：同态聚合耗时
    he = m["privacy"]["he"]
    fig, ax = plt.subplots(figsize=(5.6, 3.1))
    parties = [h["parties"] for h in he]
    enc = [h["encrypt_ms"] for h in he]
    dec = [h["decrypt_ms"] for h in he]
    w = 0.36
    idx = np.arange(len(parties))
    ax.bar(idx - w / 2, enc, w, label="单方加密", color=accent)
    ax.bar(idx + w / 2, dec, w, label="解密合计", color=ok)
    ax.set_xticks(idx)
    ax.set_xticklabels([f"{p} 方" for p in parties])
    ax.set_ylabel("耗时（ms）")
    ax.set_title("Paillier 同态聚合开销（1024 bit 模数）", fontsize=10.5)
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    p = CHARTS / "chart_he.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    files.append(str(p))
    return files


# --------------------------------------------------------------------- 主流程
def main() -> dict:
    print("=" * 72)
    print("数元链基准与安全实验")
    print("=" * 72)
    metrics: dict = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()}",
            "cpu": platform.processor() or "—",
            "validator_nodes": C.VALIDATOR_COUNT,
            "quorum": C.QUORUM,
            "fp_bits": C.FINGERPRINT_BITS,
            "fp_quantum": C.FINGERPRINT_QUANTUM,
        }
    }

    steps = [
        ("端到端六幕演示", bench_e2e, "e2e"),
        ("交易吞吐与时延", bench_throughput, "throughput"),
        ("分账守恒（1000 次调用）", bench_settlement, "settlement"),
        ("授权策略拦截", bench_policy_guard, "policy"),
        ("指纹检出与误报", bench_fingerprint, "fingerprint"),
        ("同态加密与差分隐私", bench_privacy, "privacy"),
        ("共识容错与可验证性", bench_consensus, "consensus"),
    ]
    for label, fn, key in steps:
        t0 = time.perf_counter()
        print(f"  → {label} …", end="", flush=True)
        metrics[key] = fn()
        print(f" 完成（{time.perf_counter() - t0:.1f}s）")

    print("  → 生成图表 …", end="", flush=True)
    metrics["charts"] = make_charts(metrics)
    print(" 完成")

    out = RESULTS / "metrics.json"
    out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n指标已写入 {out}")

    tp = metrics["throughput"]
    st = metrics["settlement"]
    print(f"  TPS {tp['tps']} ｜ P95 时延 {tp['latency_ms']['p95']} ms ｜ 只读 QPS {tp['read_qps']}")
    print(f"  分账守恒误差 {st['conservation_error_micro']} 微元（{st['calls']} 次调用）")
    print(f"  策略拦截率 {metrics['policy']['interception_rate']}%")
    print(f"  指纹误报 {metrics['fingerprint']['false_positive']}/{metrics['fingerprint']['fpr_trials']}")
    return metrics


if __name__ == "__main__":
    main()
