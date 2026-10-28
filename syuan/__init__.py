"""数元链 —— 公共数据授权运营的数据元件确权、计量与自动分账系统。

本包为参赛作品的可执行实现，包含：
    ledger          联盟链内核（区块、Merkle、多签共识、多节点副本）
    contracts       合约运行时与四份业务合约
    privacy         Paillier 同态加密与差分隐私
    fingerprint     数据元件鲁棒指纹（嵌入 / 提取 / 变换攻击）
    scenario        六幕演示场景编排
    service         Flask 服务与 Web 演示台
"""

__version__ = "1.0.0"
__all__ = ["config", "crypto_utils", "ledger", "engine", "scenario"]
