/* 数元链演示台前端：仅依赖原生 DOM，无构建步骤。 */

const $ = (s) => document.querySelector(s);
const fmtYuan = (micro) =>
  "¥" + (micro / 1e6).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtNum = (n, d = 0) => Number(n).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const ACTS = [
  { n: 1, t: "元件确权登记", d: "内容哈希 / 字段模式哈希 / 血缘父节点" },
  { n: 2, t: "授权签发", d: "用途 · 次数 · 有效期写进合约" },
  { n: 3, t: "调用与自动分账", d: "扣次 + 沿血缘递归分配" },
  { n: 4, t: "泄漏溯源与冻结", d: "指纹命中 → 立案 → 冻结全部授权" },
  { n: 5, t: "策略闸门", d: "用途 / 次数 / 身份 / 越权四重拦截" },
  { n: 6, t: "同态聚合 + 差分隐私", d: "密文求和，只放出均值" },
];

const TOPICS = {
  ParticipantRegistered: (p) => [`联盟准入：${p.name}`, `roles=${(p.roles || []).join("/")}`, "ok"],
  ElementRegistered: (p) => [`元件登记 ${p.element_id}`, `${p.name} · 深度 ${p.depth} · ${p.content_hash.slice(0, 10)}…`, "tx"],
  FingerprintRegistered: (p) => [`指纹登记 ${p.element_id}`, `${p.bits} bit · Δ=${p.quantum}`, "tx"],
  PriceUpdated: (p) => [`调价 ${p.element_id}`, fmtYuan(p.price_micro) + " / 次", "tx"],
  AccessGranted: (p) => [`授权签发 ${p.policy_id.slice(0, 10)}…`, `${p.purpose} · ${p.max_calls} 次 · ${fmtYuan(p.price_micro)}/次`, "tx"],
  AccessConsumed: (p) => [`调用第 ${p.seq} 次 ${p.element_id}`, `剩余 ${p.remaining} 次 · 结算 ${fmtYuan(p.price_micro)} · ${(p.allocation || []).length} 方分账`, "tx"],
  AccessRevoked: (p) => [`授权撤销 ${p.policy_id.slice(0, 10)}…`, p.reason || "", "warn"],
  Settled: (p) => [`结算 #${p.seq} ${fmtYuan(p.amount_micro)}`, (p.parts || []).map((x) => `${x.name} ${fmtYuan(x.amount_micro)}`).join(" / "), "ok"],
  PoliciesFrozen: (p) => [`批量冻结授权 ×${p.count}`, `${p.element_id} · 案件 ${p.case_id}`, "bad"],
  ElementFrozen: (p) => [`元件冻结 ${p.element_id}`, p.reason || "", "bad"],
  LeakDetected: (p) => [`泄漏命中 ${p.case_id}`, `相似度 ${(p.similarity_bp / 100).toFixed(1)}% · 冻结 ${p.frozen_policies} 条授权`, "bad"],
};

let BUSY = false;
let LAST_ACT = 0;

async function api(path, method = "GET") {
  const res = await fetch(path, { method });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function toast(msg, ms = 2600) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), ms);
}

/* ------------------------------------------------------------------ 顶栏 */
function renderStats(s) {
  $("#m-height").textContent = s.stats.height;
  $("#m-txs").textContent = fmtNum(s.stats.txs);
  $("#m-tps").textContent = s.stats.avg_block_ms ? s.stats.avg_block_ms.toFixed(0) + " ms" : "—";
  $("#m-nodes").textContent = `${s.network.online} / ${s.network.validators.length}`;
  const ok = s.integrity.ok;
  const el = $("#m-int");
  el.className = "metric " + (ok ? "ok" : "bad");
  el.querySelector("strong").textContent = ok ? `通过 · ${s.integrity.blocks} 块` : "异常";
}

/* ------------------------------------------------------------------ 左栏 */
function renderActs(done) {
  const box = $("#acts");
  box.innerHTML = "";
  ACTS.forEach((a) => {
    const b = document.createElement("button");
    b.className = "act" + (done.includes(a.n) ? " done" : "");
    b.disabled = BUSY;
    b.innerHTML = `<span class="num">${done.includes(a.n) ? "✓" : a.n}</span>
      <span><span class="t">${a.t}</span><span class="d">${a.d}</span></span>`;
    b.onclick = () => runAct(a.n);
    box.appendChild(b);
  });
}

function renderMembers(list) {
  $("#pcount").textContent = `${list.length} 个主体`;
  $("#members").innerHTML = list
    .map(
      (m) => `<div class="member"><span class="dot"></span>
        <span class="info"><span class="n">${esc(m.name)}</span><span class="r">${esc(
          m.roles.map((r) => r.replace("ROLE_", "")).join("/")
        )}</span><span class="a">${esc(m.short)}</span></span></div>`
    )
    .join("");
}

/* ------------------------------------------------------------------ 血缘 DAG */
function renderDag(elements) {
  const box = $("#dag");
  if (!elements.length) {
    box.innerHTML = '<div class="empty">尚未登记元件</div>';
    return;
  }
  const W = 208, H = 86, GX = 96, GY = 26;
  const byDepth = {};
  elements.forEach((e) => (byDepth[e.depth] = byDepth[e.depth] || []).push(e));
  const pos = {};
  let maxRows = 1;
  Object.keys(byDepth).forEach((d) => {
    byDepth[d].forEach((e, i) => {
      pos[e.id] = { x: 18 + Number(d) * (W + GX), y: 18 + i * (H + GY) };
    });
    maxRows = Math.max(maxRows, byDepth[d].length);
  });
  const width = 18 + (Object.keys(byDepth).length - 1) * (W + GX) + W + 18;
  const height = 18 + maxRows * (H + GY);

  let edges = "";
  elements.forEach((e) => {
    (e.parents || []).forEach((p) => {
      const a = pos[p.id], b = pos[e.id];
      if (!a || !b) return;
      const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2;
      const mx = (x1 + x2) / 2;
      edges += `<path d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" fill="none" stroke="#94a3b8" stroke-width="1.4" marker-end="url(#arrow)"/>`;
      edges += `<text class="edge-label" x="${mx - 6}" y="${(y1 + y2) / 2 - 5}">${p.weight}</text>`;
    });
  });

  let nodes = "";
  elements.forEach((e) => {
    const p = pos[e.id];
    const frozen = e.status === "FROZEN";
    const isDerived = (e.parents || []).length > 0;
    nodes += `<g class="node-box" transform="translate(${p.x},${p.y})">
      <rect width="${W}" height="${H}" rx="12" fill="${frozen ? "#fff8f7" : "#ffffff"}" stroke="${frozen ? "#f0b7b1" : "#dbe3ef"}" stroke-width="1.2"/>
      <rect width="4" height="${H}" rx="2" fill="${frozen ? "#c0392b" : isDerived ? "#1d4ed8" : "#0f8f6a"}"/>
      <text class="node-title" x="14" y="22">${esc(e.name.length > 13 ? e.name.slice(0, 13) + "…" : e.name)}</text>
      <text class="node-meta" x="14" y="40">${esc(e.element_id)} · ${esc(e.owner_name)}</text>
      <text class="node-price" x="14" y="60">${fmtYuan(e.price_micro)} / 次</text>
      <text class="node-meta" x="14" y="76">${e.rows} 行 · ${esc(e.short_hash)}</text>
      <g transform="translate(${W - 62},10)">
        <rect width="52" height="18" rx="9" fill="${frozen ? "#fdeceb" : isDerived ? "#eef2ff" : "#e6f6f0"}"/>
        <text x="26" y="12.5" text-anchor="middle" font-size="10" font-weight="650"
          fill="${frozen ? "#c0392b" : isDerived ? "#1d4ed8" : "#0f8f6a"}">${frozen ? "已冻结" : isDerived ? "派生" : "原始"}</text>
      </g>
    </g>`;
  });

  box.innerHTML = `<svg class="dag" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}">
    <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
      <path d="M0,0 L10,5 L0,10 z" fill="#94a3b8"/></marker></defs>
    ${edges}${nodes}</svg>`;
}

/* ------------------------------------------------------------------ 授权策略 */
function renderPolicies(list) {
  const box = $("#policies");
  if (!list.length) {
    box.innerHTML = '<div class="empty">尚无授权</div>';
    return;
  }
  const stateCls = { ACTIVE: "active", FROZEN: "frozen", EXHAUSTED: "frozen", EXPIRED: "frozen", REVOKED: "frozen" };
  box.innerHTML = list
    .map((p) => {
      const pct = Math.min(100, (p.used / p.max_calls) * 100);
      return `<div class="row">
        <div class="main">
          <div class="title">${esc(p.element_name)} <span class="badge ${stateCls[p.state] || "active"}">${p.state}</span></div>
          <div class="sub">${esc(p.consumer_name)} · 用途「${esc(p.purpose)}」 · ${fmtYuan(p.price_micro)}/次 · 到期 ${fmtTime(p.expire_at)}</div>
          <div class="progress"><i style="width:${pct}%"></i></div>
        </div>
        <div class="right"><b>${p.used} / ${p.max_calls}</b><div class="sub">${p.remaining} 次可用</div></div>
      </div>`;
    })
    .join("");
}

/* ------------------------------------------------------------------ 事件流 */
function renderEvents(events) {
  const box = $("#events");
  if (!events.length) {
    box.innerHTML = '<div class="empty">等待交易…</div>';
    return;
  }
  box.innerHTML = events
    .map((e) => {
      const f = TOPICS[e.topic];
      const [title, detail, kind] = f ? f(e.payload) : [e.topic, "", "tx"];
      return `<div class="ev ${kind}">
        <div class="h">#${e.height == null ? "?" : e.height}</div>
        <div class="b"><span class="topic">${esc(title)}</span><br>${esc(detail)}
        <div class="x">${esc(e.contract)} · tx ${esc(e.txid.slice(0, 12))}…</div></div>
      </div>`;
    })
    .join("");
}

/* ------------------------------------------------------------------ 日志 */
function renderLogs(logs) {
  const box = $("#logs");
  const all = [];
  Object.keys(logs || {})
    .sort()
    .forEach((k) => (logs[k] || []).forEach((l) => all.push(l)));
  if (!all.length) {
    box.innerHTML = '<div class="empty">尚未开始</div>';
    return;
  }
  box.innerHTML = all
    .map((l) => `<div class="log-line ${esc(l.kind)}">${esc(l.text)}</div>`)
    .join("");
}

/* ------------------------------------------------------------------ 分账 */
function renderBalances(list, settle) {
  const box = $("#balances");
  $("#settle-hint").textContent = settle && settle.settlements ? `${settle.settlements} 次结算 · ${fmtYuan(settle.volume_micro)}` : "—";
  if (!list.length) {
    box.innerHTML = '<div class="empty">尚无结算</div>';
    return;
  }
  const max = Math.max(...list.map((b) => b.balance_micro), 1);
  box.innerHTML = list
    .map(
      (b) => `<div class="bal"><div class="t"><span>${esc(b.name)}</span><b>${fmtYuan(b.balance_micro)}</b></div>
      <div class="bar"><i style="width:${(b.balance_micro / max) * 100}%"></i></div></div>`
    )
    .join("");
}

/* ------------------------------------------------------------------ 隐私 */
function renderPrivacy(pv) {
  const box = $("#privacy");
  if (!pv) {
    box.innerHTML = '<div class="empty">运行第六幕后显示</div>';
    return;
  }
  const err = Math.abs(pv.noisy_mean - pv.true_mean) / pv.true_mean * 100;
  box.innerHTML = `
    <div class="kv"><span>模数强度</span><b>${pv.key_bits} bit</b></div>
    <div class="kv"><span>密文长度</span><b>${fmtNum(pv.ct_len)} 字符</b></div>
    ${(pv.ciphertexts || [])
      .map((c) => `<div class="ct"><b style="color:#64748b">${esc(c.site)}</b><br>${esc(c.ciphertext)}</div>`)
      .join("")}
    <div class="kv"><span>行业总用电（解密后）</span><b>${fmtNum(pv.total_kwh)} kWh</b></div>
    <div class="kv"><span>企业月均（真实）</span><b>${fmtNum(pv.true_mean)} kWh</b></div>
    <div class="kv"><span>发布值（ε=${pv.epsilon}）</span><b class="acc">${fmtNum(pv.noisy_mean)} kWh</b></div>
    <div class="kv"><span>相对误差</span><b>${err.toFixed(2)}%</b></div>
    <div class="meter"><i style="width:${Math.min(100, err * 12)}%"></i></div>
    <div class="sub" style="font-size:10.5px;color:#94a3b8;margin-top:6px">各站点仅上传密文，明文始终不出本地；聚合方只解出总和。</div>`;
}

/* ------------------------------------------------------------------ 取证 */
function renderForensics(leak, cases) {
  const box = $("#forensics");
  const c = (cases || [])[0];
  if (!leak || !c) {
    box.innerHTML = '<div class="empty">运行第四幕后显示</div>';
    return;
  }
  const sim = c.similarity_bp / 100;
  box.innerHTML = `
    <div class="kv"><span>案件编号</span><b>${esc(c.case_id)}</b></div>
    <div class="kv"><span>命中元件</span><b>${esc(c.element_name)}</b></div>
    <div class="kv"><span>指纹相似度</span><b class="acc">${sim.toFixed(1)}%</b></div>
    <div class="meter"><i style="width:${sim}%"></i></div>
    <div class="kv"><span>判定门限</span><b>85.0%</b></div>
    <div class="kv"><span>冻结授权</span><b>${c.frozen_policies} 条</b></div>
    <div class="kv"><span>证据哈希</span><b style="font-family:var(--mono);font-size:10px">${esc(c.evidence_hash.slice(0, 16))}…</b></div>
    <div class="sub" style="margin-top:8px;font-size:11px;color:#64748b">
      链上排查清单：${(c.consumers || []).map((x) => esc(x.consumer_name) + `(${x.used}次)`).join("、") || "—"}
    </div>`;
}

/* ------------------------------------------------------------------ 区块 */
function renderBlocks(blocks) {
  const box = $("#blocks");
  if (!blocks.length) {
    box.innerHTML = '<div class="empty">尚无区块</div>';
    return;
  }
  box.innerHTML = blocks
    .map(
      (b) => `<details class="block">
      <summary><span class="bh">#${b.height}</span>
        <span class="hh">${b.hash.slice(0, 12)}…</span>
        <span class="hh">${b.proposer}</span>
        <span class="cnt">${b.tx_count} tx · ${b.sigs}/4 签</span></summary>
      <div style="font-size:10px;color:#94a3b8;font-family:var(--mono);margin-top:6px">
        state ${b.state_root.slice(0, 14)}… · merkle ${b.merkle.slice(0, 14)}… · gas ${fmtNum(b.gas_used)}
      </div>
      <div class="txs">${(b.txs || [])
        .map(
          (t) => `<div class="tx"><span class="m">${esc(t.method)}</span>
          <span style="color:#64748b">${esc((t.sender || "").slice(0, 10))}…</span>
          <span class="s SUCCESS" style="margin-left:auto">${esc(t.txid.slice(0, 8))}</span></div>`
        )
        .join("")}</div>
    </details>`
    )
    .join("");
}

/* ------------------------------------------------------------------ 总装 */
function render(s) {
  renderStats(s);
  renderActs(s.acts || []);
  renderMembers(s.participants || []);
  renderDag(s.elements || []);
  renderPolicies(s.policies || []);
  renderEvents(s.events || []);
  renderLogs(s.logs || {});
  renderBalances(s.balances || [], s.settlement);
  renderPrivacy(s.meta && s.meta.privacy);
  renderForensics(s.meta && s.meta.leak, s.cases || []);
  renderBlocks(s.blocks || []);
  LAST_ACT = Math.max(...(s.acts || [0]));
}

async function refresh() {
  if (BUSY) return;
  try {
    render(await api("/api/state"));
  } catch (e) {
    console.error(e);
  }
}

async function runAct(n) {
  if (BUSY) return;
  BUSY = true;
  renderActs([...document.querySelectorAll(".act.done")].map((_, i) => i + 1));
  document.querySelectorAll(".act").forEach((b) => (b.disabled = true));
  $("#run-all").disabled = true;
  try {
    const res = await api(`/api/act/${n}`, "POST");
    render(res.state);
    toast(`第 ${n} 幕完成：${ACTS[n - 1].t}`);
  } catch (e) {
    toast("执行失败：" + e.message, 5000);
  } finally {
    BUSY = false;
    $("#run-all").disabled = false;
    refresh();
  }
}

async function runAll() {
  if (BUSY) return;
  BUSY = true;
  document.querySelectorAll(".act").forEach((b) => (b.disabled = true));
  $("#run-all").disabled = true;
  try {
    for (let i = 1; i <= 6; i++) {
      const res = await api(`/api/act/${i}`, "POST");
      render(res.state);
      await new Promise((r) => setTimeout(r, 620));
    }
    toast("六幕演示完成");
  } catch (e) {
    toast("执行失败：" + e.message, 5000);
  } finally {
    BUSY = false;
    $("#run-all").disabled = false;
    refresh();
  }
}

async function reset() {
  if (BUSY) return;
  BUSY = true;
  try {
    render(await api("/api/reset", "POST"));
    toast("链与数据集已重置");
  } catch (e) {
    toast("重置失败：" + e.message, 4000);
  } finally {
    BUSY = false;
  }
}

$("#run-all").onclick = runAll;
$("#reset").onclick = reset;
refresh();
setInterval(refresh, 2500);
