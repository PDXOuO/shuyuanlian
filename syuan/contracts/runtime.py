"""合约运行时：状态存储、revert 语义、事件、跨合约调用上下文。"""

from __future__ import annotations

import copy
from typing import Any

from .. import config as C
from ..crypto_utils import hash_obj


class Revert(Exception):
    """合约执行失败并回滚，对应 EVM 的 revert。错误码用于函评演示中的"当场拒绝"。"""

    def __init__(self, code: str, msg: str = ""):
        self.code = code
        self.msg = msg or code
        super().__init__(f"{code}: {self.msg}")


def _clone(value: Any) -> Any:
    """结构化拷贝：只复制容器，标量（str/int/float/bool/None）本身不可变，直接复用。

    链上状态是 JSON 语义数据，用这个比 copy.deepcopy 快一个量级。
    """
    t = type(value)
    if t is dict:
        return {k: _clone(v) for k, v in value.items()}
    if t is list:
        return [_clone(v) for v in value]
    return value


class StateStore:
    """扁平 KV 状态（键形如 "AccessPolicy:policy:0x.."），支持快照回滚与状态根计算。"""

    def __init__(self, data: dict[str, Any] | None = None):
        self.data: dict[str, Any] = data if data is not None else {}
        self.reads = 0
        self.writes = 0
        # 写前撤销日志：回滚代价 O(写次数)，而不是 O(状态大小) 的深拷贝
        self._undo: list[tuple[str, bool, Any]] | None = None

    # ---------------- KV
    # 读一律返回副本：撤销日志按引用记录旧值，对 dict 的原地修改它看不见，
    # 一旦把已提交对象的引用交出去，任何一次只读查询都可能悄悄改写状态、破坏状态根。
    def get(self, key: str, default: Any = None) -> Any:
        self.reads += 1
        value = self.data.get(key, default)
        return _clone(value) if value is not None else default

    def set(self, key: str, value: Any) -> None:
        if self._undo is not None:
            self._undo.append((key, key in self.data, self.data.get(key)))
        self.writes += 1
        self.data[key] = value

    def delete(self, key: str) -> None:
        if self._undo is not None and key in self.data:
            self._undo.append((key, True, self.data[key]))
        self.writes += 1
        self.data.pop(key, None)

    def keys(self, prefix: str) -> list[str]:
        self.reads += 1
        return [k for k in self.data if k.startswith(prefix)]

    def scan(self, prefix: str) -> list[Any]:
        return [self.get(k) for k in self.keys(prefix)]

    # ---------------- 事务
    def begin(self) -> None:
        self._undo = []

    def commit(self) -> None:
        self._undo = None

    def rollback(self) -> None:
        if self._undo is None:
            return
        for key, existed, old in reversed(self._undo):
            if existed:
                self.data[key] = old
            else:
                self.data.pop(key, None)
        self._undo = None

    # ---------------- 快照（用于离线导出，不在热路径上）
    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)

    def restore(self, snap: dict[str, Any]) -> None:
        self.data = snap

    def root(self) -> str:
        return hash_obj(self.data)

    def reset_meters(self) -> None:
        self.reads = 0
        self.writes = 0


class ExecContext:
    """一次交易执行的上下文（发送者、时间戳、事件缓冲、调用深度）。"""

    def __init__(self, state: StateStore, sender: str, ts: float, engine: Any):
        self.state = state
        self.sender = sender
        self.ts = ts
        self.engine = engine
        self.events: list[dict[str, Any]] = []
        self.depth = 0
        self.caller: str | None = None  # 内部调用时记录调用方合约地址

    def call(self, addr: str, method: str, args: dict[str, Any]) -> Any:
        """同步跨合约调用（内部消息），共享回滚域。"""
        if self.depth > 6:
            raise Revert("E_DEPTH", "合约调用深度超限")
        return self.engine.dispatch(addr, method, args, self, self.depth + 1)

    def emit(self, contract: str, topic: str, payload: dict[str, Any]) -> None:
        self.events.append({"contract": contract, "topic": topic, "payload": payload, "ts": self.ts})

    def require_role(self, role: str) -> None:
        roles = self.engine.roles.get(self.sender, [])
        if role not in roles and C.ROLE_ADMIN not in roles:
            raise Revert("E_ROLE", f"地址 {self.sender[:10]}… 缺少角色 {role}")

    def require(self, cond: bool, code: str, msg: str = "") -> None:
        if not cond:
            raise Revert(code, msg)


class Contract:
    """业务合约基类。方法以 m_ 前缀暴露，与 Solidity 合约接口一一对应。"""

    name = "Contract"
    addr = "0x"

    def __init__(self, ctx: ExecContext):
        self.ctx = ctx
        self.state = ctx.state

    # ---------------- 工具
    def key(self, *parts: str) -> str:
        return ":".join((self.name, *parts))

    def emit(self, topic: str, payload: dict[str, Any]) -> None:
        self.ctx.emit(self.name, topic, payload)

    def require(self, cond: bool, code: str, msg: str = "") -> None:
        self.ctx.require(cond, code, msg)

    def call(self, addr: str, method: str, args: dict[str, Any]) -> Any:
        """跨合约内部调用；调用期间 ctx.caller 指向本合约，供被调方做权限判定。"""
        prev = self.ctx.caller
        self.ctx.caller = self.addr
        try:
            return self.ctx.call(addr, method, args)
        finally:
            self.ctx.caller = prev

    @property
    def now(self) -> float:
        return self.ctx.ts

    # ---------------- 分发
    def dispatch(self, method: str, args: dict[str, Any]) -> Any:
        fn = getattr(self, f"m_{method}", None)
        if fn is None:
            raise Revert("E_METHOD", f"{self.name} 不存在方法 {method}")
        try:
            return fn(**(args or {}))
        except TypeError as exc:  # 参数不匹配视作非法调用
            raise Revert("E_ARGS", f"{self.name}.{method} 参数错误：{exc}") from exc

    # ---------------- 通用视图
    def paginate(self, items: list[Any], limit: int = 100) -> list[Any]:
        return items[: max(1, min(int(limit), 500))]


__all__ = ["Revert", "StateStore", "ExecContext", "Contract"]
