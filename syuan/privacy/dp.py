"""差分隐私：拉普拉斯机制，用于同态聚合结果的发布保护。"""

from __future__ import annotations

import math
import secrets


def laplace_noise(scale: float) -> float:
    """逆变换采样：u ~ U(-0.5, 0.5)，x = -scale * sign(u) * ln(1 - 2|u|)。"""
    u = secrets.randbelow(1 << 53) / (1 << 53) - 0.5
    return -scale * (1 if u >= 0 else -1) * math.log(1 - 2 * abs(u))


def laplace_mechanism(value: float, sensitivity: float, epsilon: float) -> float:
    if epsilon <= 0:
        raise ValueError("epsilon 必须为正")
    return value + laplace_noise(sensitivity / epsilon)


def mean_sensitivity(bound: float, n: int) -> float:
    """有界数据集上均值的 L1 敏感度 = bound / n。"""
    return bound / max(1, n)


def dp_curve(bound: float, n: int, eps_list: list[float], trials: int = 500) -> list[dict]:
    """给定真值 0 的均值扰动幅度，量化不同 ε 下的误差。"""
    sens = mean_sensitivity(bound, n)
    out = []
    for eps in eps_list:
        errs = [abs(laplace_noise(sens / eps)) for _ in range(trials)]
        out.append(
            {
                "epsilon": eps,
                "scale": sens / eps,
                "mean_abs_err": sum(errs) / len(errs),
                "p95_abs_err": sorted(errs)[int(0.95 * len(errs)) - 1],
            }
        )
    return out


__all__ = ["laplace_noise", "laplace_mechanism", "mean_sensitivity", "dp_curve"]
