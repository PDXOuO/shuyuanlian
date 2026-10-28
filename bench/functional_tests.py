"""功能测试：24 条用例（含 14 条异常路径），结果写入 results/testcases.json。

    python bench/functional_tests.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syuan import config as C  # noqa: E402
from syuan.crypto_utils import sha256_hex  # noqa: E402
from syuan.engine import ChainEngine  # noqa: E402
from syuan.fingerprint import RobustFingerprint, t_noise, t_sample  # noqa: E402
from syuan.privacy import load_or_create_keypair  # noqa: E402


def run() -> dict:
    e = ChainEngine()
    fp = RobustFingerprint()
    rows: list[dict] = []

    def record(cid, name, expect, status, code, detail=""):
        ok = (expect == "SUCCESS" and status == "SUCCESS") or (
            expect != "SUCCESS" and status == "REVERTED" and code == expect
        ) or (expect == "PASS" and status == "PASS")
        rows.append(
            {
                "id": cid,
                "case": name,
                "expect": expect,
                "actual": code or status,
                "status": status,
                "pass": bool(ok),
                "detail": detail,
            }
        )

    def tx(cid, name, expect, fn, detail=""):
        r = fn()
        record(cid, name, expect, r["status"], r["code"], detail or r.get("message", ""))

    # ---------------------------------------------------------------- 元件
    h = "a" * 64
    tx("ELEM-01", "登记原始元件（持有方签名）", "SUCCESS",
       lambda: e.invoke_as("park", C.ADDR_REGISTRY, "registerElement",
                           {"element_id": "EL-T1", "name": "测试原始元件", "content_hash": h,
                            "schema_hash": "b" * 64, "price_micro": 10_000_000, "rows": 100}))
    tx("ELEM-02", "重复元件 ID 登记", "E_DUP",
       lambda: e.invoke_as("park", C.ADDR_REGISTRY, "registerElement",
                           {"element_id": "EL-T1", "name": "重复", "content_hash": h, "schema_hash": "b" * 64}))
    tx("ELEM-03", "父节点不存在", "E_PARENT",
       lambda: e.invoke_as("processor", C.ADDR_REGISTRY, "registerElement",
                           {"element_id": "EL-T2", "name": "孤儿元件", "content_hash": h, "schema_hash": "b" * 64,
                            "parents": [{"id": "EL-NOT-EXIST", "weight": 1}]}))
    tx("ELEM-04", "派生元件登记（血缘深度 1）", "SUCCESS",
       lambda: e.invoke_as("processor", C.ADDR_REGISTRY, "registerElement",
                           {"element_id": "EL-T2", "name": "派生元件", "content_hash": "c" * 64,
                            "schema_hash": "d" * 64, "parents": [{"id": "EL-T1", "weight": 3}],
                            "price_micro": 20_000_000, "self_share_bp": 6000}))
    el = e.call(C.ADDR_REGISTRY, "getElement", {"element_id": "EL-T2"})
    record("ELEM-05", "血缘深度与父节点正确落库", "PASS", "PASS" if el["depth"] == 1 and el["parents"][0]["weight"] == 3 else "FAIL",
           "depth=%d" % el["depth"], f"depth={el['depth']}, parents={len(el['parents'])}")

    # ---------------------------------------------------------------- 授权
    g = e.invoke_as("stat", C.ADDR_REGISTRY, "registerElement",
                    {"element_id": "EL-T3", "name": "统计方元件", "content_hash": "e" * 64, "schema_hash": "f" * 64,
                     "price_micro": 5_000_000, "rows": 50})
    tx("AUTH-01", "持有者签发授权", "SUCCESS",
       lambda: e.invoke_as("park", C.ADDR_ACCESS, "grant",
                           {"element_id": "EL-T1", "consumer": e.addr("bank"), "purpose": "风控建模",
                            "max_calls": 5, "ttl_seconds": 3600, "price_micro": 10_000_000}))
    pid = e.call(C.ADDR_ACCESS, "listPolicies", {"element_id": "EL-T1"})[0]["policy_id"]
    tx("AUTH-02", "非持有者签发授权", "E_OWNER",
       lambda: e.invoke_as("bank", C.ADDR_ACCESS, "grant",
                           {"element_id": "EL-T1", "consumer": e.addr("bank"), "purpose": "风控建模",
                            "max_calls": 5, "ttl_seconds": 3600}))
    tx("AUTH-03", "向自身签发授权", "E_SELF",
       lambda: e.invoke_as("park", C.ADDR_ACCESS, "grant",
                           {"element_id": "EL-T1", "consumer": e.addr("park"), "purpose": "自用",
                            "max_calls": 5, "ttl_seconds": 3600}))
    tx("AUTH-04", "用途不在白名单内", "E_PURPOSE_MISMATCH",
       lambda: e.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "广告投放"}))
    tx("AUTH-05", "合规调用（第 1 次）", "SUCCESS",
       lambda: e.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "风控建模"}))
    tx("AUTH-06", "非授权主体冒用凭证", "E_CONSUMER",
       lambda: e.invoke_as("reseller", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "风控建模"}))
    # 过期：签发 1 秒有效期，等待后调用
    gp = e.invoke_as("park", C.ADDR_ACCESS, "grant",
                     {"element_id": "EL-T1", "consumer": e.addr("oracle"), "purpose": "核对",
                      "max_calls": 3, "ttl_seconds": 1, "price_micro": 1_000_000})
    pid_exp = gp["result"]["policy_id"]
    time.sleep(1.2)
    tx("AUTH-07", "超过有效期后调用", "E_EXPIRED",
       lambda: e.invoke_as("oracle", C.ADDR_ACCESS, "consume", {"policy_id": pid_exp, "purpose": "核对"}))
    # 撤销
    gp2 = e.invoke_as("park", C.ADDR_ACCESS, "grant",
                      {"element_id": "EL-T1", "consumer": e.addr("processor"), "purpose": "加工",
                       "max_calls": 3, "ttl_seconds": 3600, "price_micro": 1_000_000})
    pid_rv = gp2["result"]["policy_id"]
    e.invoke_as("park", C.ADDR_ACCESS, "revoke", {"policy_id": pid_rv, "reason": "持有人撤回"})
    tx("AUTH-08", "授权撤销后调用", "E_REVOKED",
       lambda: e.invoke_as("processor", C.ADDR_ACCESS, "consume", {"policy_id": pid_rv, "purpose": "加工"}))
    # 次数耗尽
    for _ in range(4):
        e.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "风控建模"})
    tx("AUTH-09", "次数耗尽后继续调用", "E_EXHAUSTED",
       lambda: e.invoke_as("bank", C.ADDR_ACCESS, "consume", {"policy_id": pid, "purpose": "风控建模"}))

    # ---------------------------------------------------------------- 分账
    ev = [x for x in e.events if x["topic"] == "Settled"]
    diffs = [sum(p["amount_micro"] for p in x["payload"]["parts"]) - x["payload"]["amount_micro"] for x in ev]
    record("SETTLE-01", "单次分账金额守恒", "PASS", "PASS" if ev and max(abs(d) for d in diffs) == 0 else "FAIL",
           "max_diff=%d" % (max(abs(d) for d in diffs) if diffs else -1), f"结算 {len(ev)} 次")
    bals = e.balances()
    total_bal = sum(b["balance_micro"] for b in bals)
    record("SETTLE-02", "累计余额等于累计收入", "PASS", "PASS" if total_bal == e.settlement_stats()["volume_micro"] else "FAIL",
           f"{total_bal}", f"余额合计 {total_bal} 微元")
    tx("SETTLE-03", "绕过授权合约直接结算", "E_AUTH",
       lambda: e.invoke_as("bank", C.ADDR_SETTLEMENT, "settle",
                           {"element_id": "EL-T1", "amount_micro": 1_000_000, "policy_id": pid}))

    # ---------------------------------------------------------------- 溯源
    tx("TRACE-01", "未登记指纹即上报泄漏", "E_NO_FP",
       lambda: e.invoke_as("oracle", C.ADDR_TRACE, "reportLeak",
                           {"element_id": "EL-T1", "similarity_bp": 9900, "evidence_hash": "1" * 64}))
    code = fp.code_for("EL-T1")
    tx("TRACE-02", "持有者登记指纹", "SUCCESS",
       lambda: e.invoke_as("park", C.ADDR_TRACE, "registerFingerprint",
                           {"element_id": "EL-T1", "code_hex": RobustFingerprint.bits_to_hex(code)}))
    tx("TRACE-03", "相似度低于门限上报", "E_LOW_CONF",
       lambda: e.invoke_as("oracle", C.ADDR_TRACE, "reportLeak",
                           {"element_id": "EL-T1", "similarity_bp": 6000, "evidence_hash": "2" * 64}))
    tx("TRACE-04", "非溯源机构上报", "E_ROLE",
       lambda: e.invoke_as("bank", C.ADDR_TRACE, "reportLeak",
                           {"element_id": "EL-T1", "similarity_bp": 9900, "evidence_hash": "3" * 64}))
    tx("TRACE-05", "命中上报并立案", "SUCCESS",
       lambda: e.invoke_as("oracle", C.ADDR_TRACE, "reportLeak",
                           {"element_id": "EL-T1", "similarity_bp": 10000, "evidence_hash": "4" * 64,
                            "sample_source": "第三方渠道样本"}))
    g2 = e.invoke_as("park", C.ADDR_ACCESS, "grant",
                     {"element_id": "EL-T1", "consumer": e.addr("bank"), "purpose": "风控建模",
                      "max_calls": 3, "ttl_seconds": 3600})
    tx("TRACE-06", "元件冻结后新增授权", "E_FROZEN",
       lambda: e.invoke_as("park", C.ADDR_ACCESS, "grant",
                           {"element_id": "EL-T1", "consumer": e.addr("reseller"), "purpose": "转售",
                            "max_calls": 3, "ttl_seconds": 3600}))
    tx("TRACE-07", "重复立案（同一元件二次上报）", "E_DUP_CASE",
       lambda: e.invoke_as("oracle", C.ADDR_TRACE, "reportLeak",
                           {"element_id": "EL-T1", "similarity_bp": 9900, "evidence_hash": "5" * 64}))

    # ---------------------------------------------------------------- 聚合锚定
    import random as _r

    kp = load_or_create_keypair(C.PAILLIER_KEY_FILE, 1024)
    vals = [_r.randint(1_000_000, 9_000_000) for _ in range(5)]
    cts = [kp.pub.encrypt(v) for v in vals]
    acc = cts[0]
    for c in cts[1:]:
        acc = kp.pub.add(acc, c)
    dec = kp.decrypt(acc)
    record("PRIV-01", "密文求和结果与明文一致", "PASS", "PASS" if dec == sum(vals) else "FAIL",
           f"{dec}", f"求和解密 {dec}，明文 {sum(vals)}")
    h = sha256_hex(str(dec))
    tx("PRIV-02", "锚定聚合轮次", "SUCCESS",
       lambda: e.invoke_as("admin", C.ADDR_ANCHOR, "anchor",
                           {"round_id": "TEST-ROUND-1", "result_hash": h, "party_count": 5, "epsilon": 1.0}))
    tx("PRIV-03", "重复锚定同一轮次", "E_DUP",
       lambda: e.invoke_as("admin", C.ADDR_ANCHOR, "anchor",
                           {"round_id": "TEST-ROUND-1", "result_hash": h, "party_count": 5, "epsilon": 1.0}))

    # ---------------------------------------------------------------- 账本
    import copy as _copy

    blk = e.chain.validators[0].blocks[-1]
    t1 = _copy.deepcopy(blk)
    t1["txs"][0]["args"]["amount_micro"] = 1
    record("CHAIN-01", "篡改区块内交易金额", "PASS",
           "PASS" if not e.chain.verify_block(t1, e.chain.validators[0].blocks[-2])[0] else "FAIL", "detected",
           e.chain.verify_block(t1, e.chain.validators[0].blocks[-2])[1])
    t2 = _copy.deepcopy(blk)
    t2["sigs"] = dict(list(t2["sigs"].items())[:2])
    record("CHAIN-02", "伪造区块签名数量不足", "PASS",
           "PASS" if not e.chain.verify_block(t2, e.chain.validators[0].blocks[-2])[0] else "FAIL", "detected",
           e.chain.verify_block(t2, e.chain.validators[0].blocks[-2])[1])
    rep = e.replay()
    record("CHAIN-03", "全链重放状态根一致", "PASS", "PASS" if rep["ok"] else "FAIL",
           f"{rep['blocks']} 块", f"{rep['txs']} 笔交易重放")

    # 回归用例：只读查询走 call() 并在结束后回滚，但撤销日志按引用记录旧值，
    # 对 dict 的原地修改它看不见。若存储层把已提交对象的引用交出去，任何一次查询
    # 都可能悄悄改写状态、且不产生区块记录，状态根从此与区块头不符。此处钉死不变量：
    # ① 存储层：改写读到的记录，不得影响已提交状态；② 引擎层：跑遍只读视图，状态根不变。
    before = e.state.root()
    probe_key = next(k for k in e.state.data if k.startswith("DataElementRegistry:element:"))
    probe = e.state.get(probe_key)
    probe["__probe__"] = True
    probe["price_micro"] = -1
    store_ok = e.state.root() == before and "__probe__" not in e.state.data[probe_key]

    e.elements()
    e.participants()
    e.policies()
    e.cases()
    e.balances()
    e.snapshot(blocks=5, events=20)
    after = e.state.root()
    leaked = [
        k for k, v in e.state.data.items()
        if isinstance(v, dict) and ("short_hash" in v or "short" in v)
    ]
    ok = store_ok and before == after and not leaked
    record("CHAIN-04", "只读查询不改变状态根", "PASS", "PASS" if ok else "FAIL",
           "root 未变" if before == after else "root 已变",
           "存储层隔离有效，未污染" if store_ok else "存储层泄露引用")

    # ---------------------------------------------------------------- 指纹鲁棒（端到端）
    from syuan import datasets

    data = datasets.materialize(fp)
    sample = t_noise(t_sample(data["tables"]["derived"], 0.4, 3), 0.0025, 5)
    det = fp.detect(sample, data["codes"]["EL-CREDIT-INDEX"])
    record("FP-01", "抽样 40% + 加噪后仍可溯源", "PASS",
           "PASS" if det["similarity_bp"] >= 8500 else "FAIL",
           f"similarity={det['similarity_bp']/100:.1f}%", f"命中 {det['matched']}/{det['bits']} bit")

    passed = sum(1 for r in rows if r["pass"])
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "pass_rate": round(passed / len(rows) * 100, 2),
        "cases": rows,
    }


def main() -> dict:
    res = run()
    path = C.RESULTS_DIR / "testcases.json"
    path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"功能测试：{res['passed']}/{res['total']} 通过（{res['pass_rate']}%）")
    for r in res["cases"]:
        mark = "OK " if r["pass"] else "FAIL"
        print(f"  [{mark}] {r['id']:<10} {r['case']:<26} 预期 {r['expect']:<18} 实际 {r['actual']}")
    print("已写入", path)
    return res


if __name__ == "__main__":
    main()
