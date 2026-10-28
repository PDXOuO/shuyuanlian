"""全局配置与常量。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
DOCS_DIR = ROOT / "docs"
DELIVER_DIR = ROOT / "deliverables"

for _d in (DATA_DIR, RESULTS_DIR, DOCS_DIR, DELIVER_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 金额与计量
# 链上一律使用整数微元（1 元 = 1_000_000 微元），杜绝浮点误差导致的"分账不平"。
MICRO = 1_000_000
BP = 10_000              # 万分比基数

# ---------------------------------------------------------------- 共识参数
VALIDATOR_COUNT = 4      # 联盟链验证节点数
QUORUM = 3               # 拜占庭容错门限：4 节点容忍 1 节点故障

# ---------------------------------------------------------------- 合约地址
# 合约以命名空间方式寻址，便于演示与审计。
ADDR_REGISTRY = "0x00000000000000000000000000000000000000c1"
ADDR_ACCESS = "0x00000000000000000000000000000000000000c2"
ADDR_SETTLEMENT = "0x00000000000000000000000000000000000000c3"
ADDR_TRACE = "0x00000000000000000000000000000000000000c4"
ADDR_ANCHOR = "0x00000000000000000000000000000000000000c5"

CONTRACT_NAMES = {
    ADDR_REGISTRY: "DataElementRegistry",
    ADDR_ACCESS: "AccessPolicy",
    ADDR_SETTLEMENT: "Settlement",
    ADDR_TRACE: "TraceAndFreeze",
    ADDR_ANCHOR: "AggregateAnchor",
}

# ---------------------------------------------------------------- 角色
ROLE_ADMIN = "ROLE_ADMIN"        # 联盟治理方
ROLE_OWNER = "ROLE_OWNER"        # 数据持有方 / 授权运营方
ROLE_CONSUMER = "ROLE_CONSUMER"  # 数据使用方
ROLE_ORACLE = "ROLE_ORACLE"      # 检测预言机（指纹检测机构）

# ---------------------------------------------------------------- 授权状态
ST_ACTIVE = "ACTIVE"
ST_EXHAUSTED = "EXHAUSTED"
ST_EXPIRED = "EXPIRED"
ST_REVOKED = "REVOKED"
ST_FROZEN = "FROZEN"

# ---------------------------------------------------------------- 业务参数
DEFAULT_SELF_SHARE_BP = 6_000     # 每次调用收入中，当前元件持有者保留 60%
DEFAULT_PARENT_SHARE_BP = 4_000   # 其余 40% 沿血缘向上游递归分配
TRACE_HIT_BP = 8_500              # 指纹相似度 >= 85% 判定为命中
FINGERPRINT_BITS = 32             # 指纹码长
FINGERPRINT_QUANTUM = 0.01        # 量化步长（对应字段保留两位小数）

# ---------------------------------------------------------------- 环境
CHAIN_FILE = DATA_DIR / "chain.json"
PAILLIER_KEY_FILE = DATA_DIR / "paillier_key.json"
LOG_LEVEL = os.environ.get("SYL_LOG_LEVEL", "INFO")
