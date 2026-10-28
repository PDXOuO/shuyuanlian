#!/usr/bin/env python3
"""数元链命令行入口。

    python main.py serve            启动演示台（默认 http://127.0.0.1:8000）
    python main.py demo             在终端跑完六幕并打印全过程
    python main.py bench            运行基准与安全实验，写出 results/metrics.json
    python main.py verify           校验账本完整性 + 全量重放一致性
    python main.py report           生成两份 PDF 报告
"""

from __future__ import annotations

import argparse
import sys


def cmd_serve(args: argparse.Namespace) -> None:
    from syuan.service import serve

    serve(host=args.host, port=args.port, debug=args.debug)


def cmd_demo(args: argparse.Namespace) -> None:
    from syuan.scenario import Demo

    demo = Demo()
    for n in range(1, 7):
        res = demo.run(n)
        print(f"\n===== 第 {n} 幕 =====")
        for line in res["logs"]:
            print(f"  [{line['kind']}] {line['text']}")
    eng = demo.engine
    print("\n链高度", eng.chain.height, "｜交易", eng.chain.tx_count(), "｜完整性", eng.chain.integrity())
    print("重放校验", eng.replay()["ok"])


def cmd_bench(args: argparse.Namespace) -> None:
    from bench.run_bench import main as run

    run()


def cmd_verify(args: argparse.Namespace) -> None:
    from syuan.scenario import Demo

    demo = Demo()
    demo.run(6)
    eng = demo.engine
    print("完整性：", eng.chain.integrity())
    rep = eng.replay()
    print("重放：", "通过" if rep["ok"] else rep["mismatches"], "｜区块", rep["blocks"], "｜交易", rep["txs"])
    proof = eng.chain.inclusion_proof(1, 0)
    print("Merkle 包含证明：", "有效" if proof["valid"] else "无效")


def cmd_report(args: argparse.Namespace) -> None:
    from docs.build_reports import main as run

    run()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shuyuanlian", description="数元链：数据元件确权、计量与自动分账")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="启动 Web 演示台")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--debug", action="store_true")
    s.set_defaults(func=cmd_serve)

    for name, fn, help_ in (
        ("demo", cmd_demo, "终端跑完六幕"),
        ("bench", cmd_bench, "运行基准测试"),
        ("verify", cmd_verify, "校验账本与重放"),
        ("report", cmd_report, "生成 PDF 报告"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(func=fn)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
