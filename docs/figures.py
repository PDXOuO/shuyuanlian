"""报告用矢量插图（SVG，直接嵌入 HTML，打印清晰）。"""

from __future__ import annotations

INK = "#0f172a"
MUTED = "#64748b"
LINE = "#cbd5e1"
BLUE = "#1d4ed8"
BLUE_BG = "#eef2ff"
TEAL = "#0f8f6a"
TEAL_BG = "#e6f6f0"
AMBER = "#b45309"
AMBER_BG = "#fdf3e3"
RED = "#c0392b"
RED_BG = "#fdeceb"
PURPLE = "#6d28d9"
PURPLE_BG = "#f3eeff"


def _box(x, y, w, h, fill="#fff", stroke=LINE, rx=8, sw=1.1):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'


def _text(x, y, s, size=10.5, fill=INK, weight="400", anchor="start", family="-apple-system,'Microsoft YaHei',sans-serif"):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
            f'text-anchor="{anchor}" font-family="{family}">{s}</text>')


def architecture() -> str:
    """五层架构图。"""
    W, H = 680, 452
    layers = [
        ("应用层", BLUE_BG, BLUE, ["元件登记台", "授权签发", "用数审计", "泄漏取证", "隐私统计"]),
        ("服务层", PURPLE_BG, PURPLE, ["REST API / SDK", "事件索引器", "指纹检测器", "同态聚合器", "治理控制台"]),
        ("合约层", TEAL_BG, TEAL, ["DataElementRegistry", "AccessPolicy", "Settlement", "TraceAndFreeze", "AggregateAnchor"]),
        ("共识层", AMBER_BG, AMBER, ["4 验证节点", "门限签名 3/4", "状态根 state_root", "全量重放校验", "Merkle 证明"]),
        ("数据层", "#f1f5f9", "#475569", ["链上状态 KV", "区块日志（只追加）", "链下数据（不出域）", "台账与案件索引", "Paillier 密钥托管"]),
    ]
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    y = 8
    for name, bg, fg, items in layers:
        h = 74
        out.append(_box(8, y, W - 16, h, fill=bg, stroke="#e2e8f0", rx=10))
        out.append(f'<rect x="8" y="{y}" width="72" height="{h}" rx="10" fill="{fg}" opacity="0.10"/>')
        out.append(_text(44, y + h / 2 + 4, name, 11.5, fg, "700", "middle"))
        n = len(items)
        pad = 90
        avail = W - 16 - pad - 12
        gap = 8
        bw = (avail - gap * (n - 1)) / n
        for i, it in enumerate(items):
            bx = 8 + pad + i * (bw + gap)
            out.append(_box(bx, y + 18, bw, h - 36, fill="#ffffff", stroke="#e2e8f0", rx=7))
            size = 8.6 if len(it) > 12 else 9.2
            out.append(_text(bx + bw / 2, y + h / 2 + 3.4, it, size, INK, "500", "middle"))
        y += h + 12
    out.append(_text(W / 2, H - 6, "实线为调用方向；合约层以下由共识层保证一致性与不可抵赖", 8.8, MUTED, "400", "middle"))
    out.append("</svg>")
    return "".join(out)


def state_machine() -> str:
    """授权状态机。"""
    W, H = 680, 262
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    out.append('<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
               f'<path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker></defs>')

    # 起始
    out.append(_box(20, 108, 76, 40, fill="#f8fafc", stroke=LINE, rx=20))
    out.append(_text(58, 133, "签发 grant", 9.2, MUTED, "600", "middle"))

    cx, cy, cw, ch = 132, 100, 118, 56
    out.append(_box(cx, cy, cw, ch, fill=TEAL_BG, stroke="#9fd8c2", rx=10, sw=1.3))
    out.append(_text(cx + cw / 2, cy + 24, "ACTIVE", 12, TEAL, "700", "middle"))
    out.append(_text(cx + cw / 2, cy + 42, "在效 · 可调用", 8.6, MUTED, "400", "middle"))
    out.append(f'<path d="M96,128 L128,128" stroke="{MUTED}" stroke-width="1.2" marker-end="url(#ar)"/>')

    # 终止态
    terms = [
        ("EXHAUSTED", "次数用尽 used=max", 320, 24, AMBER, AMBER_BG),
        ("EXPIRED", "超过有效期", 320, 96, AMBER, AMBER_BG),
        ("REVOKED", "持有者撤销", 320, 168, RED, RED_BG),
    ]
    for name, desc, x, y, fg, bg in terms:
        out.append(_box(x, y, 150, 52, fill=bg, stroke="#e5c9b3" if fg == AMBER else "#f0b7b1", rx=9))
        out.append(_text(x + 75, y + 21, name, 10.5, fg, "700", "middle"))
        out.append(_text(x + 75, y + 38, desc, 8.4, MUTED, "400", "middle"))
        out.append(f'<path d="M250,{cy + ch / 2} C290,{cy + ch / 2} 290,{y + 26} 316,{y + 26}" fill="none" '
                   f'stroke="{MUTED}" stroke-width="1.1" marker-end="url(#ar)"/>')

    # 冻结
    fx, fy = 520, 96
    out.append(_box(fx, fy, 138, 60, fill=RED_BG, stroke="#f0b7b1", rx=9, sw=1.3))
    out.append(_text(fx + 69, fy + 24, "FROZEN", 11, RED, "700", "middle"))
    out.append(_text(fx + 69, fy + 41, "泄漏命中 · 全部授权失效", 8.0, MUTED, "400", "middle"))
    out.append(f'<path d="M470,122 L516,122" stroke="{RED}" stroke-width="1.2" marker-end="url(#ar)"/>')
    out.append(_text(493, 114, "LeakDetected", 8.0, RED, "500", "middle"))
    out.append(_text(493, 136, "（合约内部调用）", 7.6, MUTED, "400", "middle"))

    # 说明
    out.append(_text(20, 232, "四种拒绝码：E_EXHAUSTED / E_EXPIRED / E_REVOKED / E_FROZEN，"
                              "另有 E_PURPOSE_MISMATCH 与 E_CONSUMER 在进入状态判断前拦截。", 8.6, MUTED))
    out.append("</svg>")
    return "".join(out)


def settlement_tree() -> str:
    """分账递归树：20 元如何在三级血缘上拆开。"""
    W, H = 680, 300
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    out.append('<defs><marker id="ar2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
               f'<path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker></defs>')

    # 根：调用
    out.append(_box(18, 118, 118, 50, fill="#f8fafc", stroke=LINE, rx=9))
    out.append(_text(77, 139, "银行调用一次", 9.4, INK, "600", "middle"))
    out.append(_text(77, 155, "支付 20.00 元", 9.0, BLUE, "700", "middle"))

    # 第一层：派生元件
    x1 = 210
    out.append(_box(x1, 108, 168, 70, fill=BLUE_BG, stroke="#c7d2fe", rx=10))
    out.append(_text(x1 + 84, 130, "产业链景气度指数", 9.6, INK, "650", "middle"))
    out.append(_text(x1 + 84, 147, "加工方 60% → 12.00", 9.0, BLUE, "700", "middle"))
    out.append(_text(x1 + 84, 164, "余 40% = 8.00 沿血缘上溯", 8.4, MUTED, "400", "middle"))
    out.append(f'<path d="M136,143 L206,143" stroke="{MUTED}" stroke-width="1.2" marker-end="url(#ar2)"/>')

    # 第二层
    x2 = 448
    out.append(_box(x2, 34, 210, 64, fill=TEAL_BG, stroke="#9fd8c2", rx=10))
    out.append(_text(x2 + 105, 55, "园区企业月度用电（权重 3）", 9.2, INK, "600", "middle"))
    out.append(_text(x2 + 105, 72, "8.00 × 3/5 = 4.80", 9.0, TEAL, "700", "middle"))
    out.append(_text(x2 + 105, 87, "原始数据 · 无上游 · 全留", 8.2, MUTED, "400", "middle"))

    out.append(_box(x2, 168, 210, 64, fill=TEAL_BG, stroke="#9fd8c2", rx=10))
    out.append(_text(x2 + 105, 189, "规上企业产值登记（权重 2）", 9.2, INK, "600", "middle"))
    out.append(_text(x2 + 105, 206, "8.00 × 2/5 = 3.20", 9.0, TEAL, "700", "middle"))
    out.append(_text(x2 + 105, 221, "原始数据 · 无上游 · 全留", 8.2, MUTED, "400", "middle"))

    out.append(f'<path d="M378,132 C410,132 410,66 444,66" fill="none" stroke="{MUTED}" stroke-width="1.2" marker-end="url(#ar2)"/>')
    out.append(f'<path d="M378,154 C410,154 410,200 444,200" fill="none" stroke="{MUTED}" stroke-width="1.2" marker-end="url(#ar2)"/>')

    # 守恒校验
    out.append(_box(18, 240, 640, 44, fill="#f8fafc", stroke=LINE, rx=9))
    out.append(_text(32, 258, "守恒校验：12.00 + 4.80 + 3.20 = 20.00（全部以微元整数运算，尾差按最大余数法分配）",
                     9.2, INK, "600"))
    out.append(_text(32, 274, "链上 1000 次连续调用实测：单次分配差额 0 微元，累计余额差额 0 微元。",
                     8.6, MUTED))
    out.append("</svg>")
    return "".join(out)


def sequence() -> str:
    """核心业务时序：登记 → 授权 → 调用 → 分账 → 取证 → 冻结。"""
    W, H = 680, 330
    actors = [
        ("数据持有方", 60, TEAL),
        ("使用方（银行）", 168, BLUE),
        ("AccessPolicy", 300, PURPLE),
        ("Settlement", 420, AMBER),
        ("TraceAndFreeze", 535, RED),
        ("联盟链", 640, INK),
    ]
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    out.append('<defs><marker id="ar3" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5.5" markerHeight="5.5" orient="auto">'
               f'<path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker></defs>')
    for name, x, color in actors:
        out.append(_box(x - 46, 6, 92, 30, fill="#f8fafc", stroke=LINE, rx=7))
        out.append(_text(x, 25, name, 8.6, color, "650", "middle"))
        out.append(f'<line x1="{x}" y1="36" x2="{x}" y2="{H - 12}" stroke="#e2e8f0" stroke-width="1" stroke-dasharray="3 3"/>')

    steps = [
        (72, 60, 300, "registerElement(内容哈希, 血缘)", "实线"),
        (100, 168, 300, "grant(用途, 次数, 有效期)", "实线"),
        (128, 300, 168, "授权凭证（policy_id）", "虚线"),
        (160, 168, 300, "consume(policy_id, purpose)", "实线"),
        (184, 300, 420, "结算 settle(金额, 元件)", "实线"),
        (208, 420, 60, "沿血缘分配 60/40 递归", "虚线"),
        (240, 535, 168, "命中告警 + 证据哈希", "实线"),
        (268, 535, 300, "freezeByLeak(element)", "实线"),
        (292, 300, 640, "写入区块（≥3/4 节点签名）", "虚线"),
    ]
    for y, x1, x2, label, style in steps:
        dash = ' stroke-dasharray="4 3"' if style == "虚线" else ""
        color = MUTED if style == "虚线" else "#64748b"
        out.append(f'<path d="M{x1},{y} L{x2},{y}" stroke="{color}" stroke-width="1.15"{dash} marker-end="url(#ar3)"/>')
        mid = (x1 + x2) / 2
        anchor = "middle"
        if abs(x2 - x1) < 110:
            mid = max(x1, x2) + 8
            anchor = "start"
        out.append(_text(mid, y - 5, label, 7.8, INK, "500", anchor))
    out.append(_text(20, H - 2, "任一步失败即整体回滚，交易仍上链留痕并返回错误码（E_*），保证「作恶有据可查」。", 8.2, MUTED))
    out.append("</svg>")
    return "".join(out)


def fingerprint_principle() -> str:
    """指纹嵌入原理：量化 → 奇偶调制 → 键控定位。"""
    W, H = 680, 208
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">']
    cells = [
        ("原始值 v", "12,345.67", "#f8fafc", INK),
        ("量化 k=round(v/Δ)", "1,234,567", "#f8fafc", INK),
        ("目标码位 b", "b[p] = 1", BLUE_BG, BLUE),
        ("奇偶校验", "k 为奇数 → 保持", "#f8fafc", INK),
        ("写入 v'", "12,345.67", TEAL_BG, TEAL),
    ]
    x = 16
    for i, (title, value, bg, fg) in enumerate(cells):
        w = 120
        out.append(_box(x, 22, w, 62, fill=bg, stroke=LINE, rx=8))
        out.append(_text(x + w / 2, 44, title, 8.4, MUTED, "500", "middle"))
        out.append(_text(x + w / 2, 66, value, 9.6, fg, "700", "middle"))
        if i < len(cells) - 1:
            out.append(f'<path d="M{x + w + 2},53 L{x + w + 14},53" stroke="{MUTED}" stroke-width="1.2" marker-end="url(#ar4)"/>')
        x += w + 16
    out.append(f'<defs><marker id="ar4" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5.5" markerHeight="5.5" '
               f'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker></defs>')
    out.append(_box(16, 104, 648, 84, fill="#f8fafc", stroke=LINE, rx=9))
    out.append(_text(30, 124, "码位由主键键控哈希决定：p = HMAC(key, row_id) mod 32", 9.2, INK, "600"))
    out.append(_text(30, 143, "→ 与行序无关，故可抵抗「行乱序 / 抽样 / 截断」；承载列按数值可解析性自动识别，故可抵抗「列重排 / 列改名 / 字段拼接」。",
                     8.6, MUTED))
    out.append(_text(30, 161, "→ 每行对全部数值列写入同一位，提取时跨列、跨行投票：432 行 × 4 列 ÷ 32 位 ≈ 54 票/位，单点扰动不影响判决。",
                     8.6, MUTED))
    out.append(_text(30, 179, "→ 代价：单值最大改动 Δ/2 = 0.005，对 10³~10⁴ 量级数据为 1e-6 相对误差（实测最大 0.01、均值 0.0025）。",
                     8.6, MUTED))
    out.append("</svg>")
    return "".join(out)
