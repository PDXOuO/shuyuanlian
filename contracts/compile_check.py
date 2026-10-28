"""Solidity 合约编译校验（离线可用，需 py-solc-x 与 solc 0.8.19）。

    python contracts/compile_check.py

作品提交时附带的 ABI/字节码会输出到 contracts/build/，便于在 FISCO BCOS 控制台直接部署。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOL_DIR = ROOT / "contracts" / "solidity"
OUT_DIR = ROOT / "contracts" / "build"
VERSION = "0.8.19"


def main() -> int:
    try:
        import solcx
    except ImportError:
        print("未安装 py-solc-x，跳过编译校验：python -m pip install py-solc-x")
        return 0

    installed = [str(v) for v in solcx.get_installed_solc_versions()]
    if VERSION not in installed:
        print(f"安装 solc {VERSION} …")
        solcx.install_solc(VERSION)
    solcx.set_solc_version(VERSION)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    for src in sorted(SOL_DIR.glob("*.sol")):
        try:
            compiled = solcx.compile_files(
                [str(src)], output_values=["abi", "bin"], solc_version=VERSION, allow_paths=str(ROOT)
            )
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  [FAIL] {src.name}: {str(exc)[:300]}")
            continue
        for key, val in compiled.items():
            name = key.split(":")[-1]
            if not val.get("bin"):
                continue  # 接口 / 抽象合约
            (OUT_DIR / f"{name}.json").write_text(
                json.dumps({"abi": val["abi"], "bytecode": "0x" + val["bin"]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(f"  [OK]   {src.name}")
    print(f"\n编译完成：{len(list(SOL_DIR.glob('*.sol')))} 个文件，失败 {failures} 个；产物目录 {OUT_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
