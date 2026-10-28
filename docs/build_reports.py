"""报告排版与 PDF 生成：HTML（A4 打印样式）→ Chrome 无头打印 → 页码脚注 → 元数据清理。"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from syuan import config as C  # noqa: E402

BUILD = ROOT / "docs" / "_build"
BUILD.mkdir(parents=True, exist_ok=True)

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

CN_FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\msyh.ttc", 0),
    (r"C:\Windows\Fonts\simhei.ttf", None),
    (r"C:\Windows\Fonts\Deng.ttf", None),
]

# --------------------------------------------------------------------------- 样式
CSS = r"""
@page { size: A4; margin: 17mm 15mm 19mm 15mm; }

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
  font-size: 10pt; line-height: 1.78; color: #1b2432;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
p { margin: 0 0 8pt; text-align: justify; text-indent: 2em; }
p.ni { text-indent: 0; }
h1 {
  font-size: 17pt; margin: 0 0 14pt; padding: 0 0 8pt;
  border-bottom: 2px solid #1d4ed8; color: #0f172a;
  page-break-before: always; page-break-after: avoid;
}
h1.first { page-break-before: avoid; }
h1 .no { color: #1d4ed8; margin-right: 10pt; font-weight: 700; }
h2 { font-size: 12.2pt; margin: 16pt 0 7pt; color: #0f172a; page-break-after: avoid; }
h2 .no { color: #1d4ed8; margin-right: 7pt; }
h3 { font-size: 10.6pt; margin: 11pt 0 5pt; color: #1e293b; page-break-after: avoid; }
ul, ol { margin: 0 0 8pt; padding-left: 22pt; }
li { margin-bottom: 3pt; text-align: justify; }
b, strong { color: #0f172a; }
code {
  font-family: "Cascadia Mono", Consolas, monospace; font-size: 8.9pt;
  background: #f1f5f9; padding: 0 3px; border-radius: 3px; color: #b91c1c;
}
pre {
  font-family: "Cascadia Mono", Consolas, monospace; font-size: 8.1pt; line-height: 1.55;
  background: #f8fafc; border: 1px solid #e2e8f0; border-left: 3px solid #1d4ed8;
  border-radius: 4px; padding: 8pt 10pt; margin: 6pt 0 10pt; white-space: pre-wrap;
  page-break-inside: avoid; color: #1e293b;
}
table {
  width: 100%; border-collapse: collapse; font-size: 8.5pt; line-height: 1.55;
  margin: 6pt 0 10pt; page-break-inside: avoid;
}
caption {
  caption-side: top; text-align: left; font-size: 8.6pt; color: #64748b;
  padding-bottom: 3pt; font-weight: 600;
}
th {
  background: #eff4ff; color: #1e3a8a; font-weight: 650; text-align: left;
  padding: 4.5pt 6pt; border: 0.6pt solid #dbe4f3;
}
td { padding: 4pt 6pt; border: 0.6pt solid #e5eaf3; vertical-align: top; }
tr:nth-child(even) td { background: #fbfcfe; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
table.compact td, table.compact th { padding: 3pt 5pt; }

.figure { margin: 8pt 0 12pt; text-align: center; page-break-inside: avoid; }
.figure img, .figure svg { max-width: 100%; }
.cap { font-size: 8.5pt; color: #64748b; margin-top: 4pt; text-align: center; }

.note {
  border-left: 3px solid #1d4ed8; background: #f5f8ff;
  padding: 8pt 11pt; margin: 8pt 0 10pt; font-size: 9.2pt;
  page-break-inside: avoid;
}
.note.warn { border-left-color: #b45309; background: #fffaf0; }
.note.ok { border-left-color: #0f8f6a; background: #f2fbf7; }
.note p { text-indent: 0; margin-bottom: 3pt; }
.note .t { font-weight: 700; color: #0f172a; display: block; margin-bottom: 2pt; }

.kv-table td:first-child { width: 26%; color: #475569; background: #f8fafc; }

/* ---------------- 封面 ---------------- */
.cover { page-break-after: always; padding-top: 6mm; }
.cover .rule { height: 3pt; background: linear-gradient(90deg, #1d4ed8 0%, #0f8f6a 62%, #ffffff 62%); margin-bottom: 14mm; }
.cover .tag { font-size: 9pt; letter-spacing: 2.4pt; color: #1d4ed8; font-weight: 700; }
.cover h1 {
  border: 0; page-break-before: avoid; font-size: 40pt; letter-spacing: 6pt;
  margin: 6mm 0 2mm; padding: 0; color: #0f172a;
}
.cover .sub { font-size: 14pt; color: #334155; line-height: 1.6; margin-bottom: 3mm; }
.cover .kind {
  display: inline-block; margin-top: 2mm; padding: 3pt 12pt; border: 1pt solid #1d4ed8;
  border-radius: 3pt; color: #1d4ed8; font-size: 10pt; letter-spacing: 3pt; font-weight: 700;
}
.cover .meta { margin-top: 20mm; width: 100%; font-size: 9.2pt; }
.cover .meta td { border: 0; border-bottom: 0.6pt solid #e2e8f0; padding: 6pt 0; }
.cover .meta td:first-child { width: 24%; color: #64748b; }
.cover .foot { position: relative; margin-top: 26mm; font-size: 8.4pt; color: #94a3b8; line-height: 1.8; }

/* ---------------- 摘要 / 目录 ---------------- */
.abstract { page-break-after: always; }
.abstract h2 { font-size: 14pt; margin-top: 0; }
.abstract .kw { font-size: 9.2pt; color: #334155; margin-top: 6pt; text-indent: 0; }
.toc { page-break-after: always; }
.toc h2 { font-size: 14pt; margin-top: 0; }
.toc ol { list-style: none; padding-left: 0; font-size: 9.6pt; }
.toc li { display: flex; align-items: baseline; margin-bottom: 5pt; }
.toc .dots { flex: 1; border-bottom: 0.6pt dotted #cbd5e1; margin: 0 6pt 0 8pt; transform: translateY(-2pt); }
.toc .pg { color: #64748b; font-variant-numeric: tabular-nums; min-width: 22pt; text-align: right; }
.toc .lv2 { padding-left: 16pt; font-size: 9.1pt; color: #334155; }
.toc .lv2 .pg { color: #94a3b8; }

.pill { display: inline-block; padding: 1pt 7pt; border-radius: 9pt; font-size: 7.8pt; font-weight: 650; }
.pill.ok { background: #e6f6f0; color: #0f8f6a; }
.pill.warn { background: #fdf3e3; color: #b45309; }
.pill.bad { background: #fdeceb; color: #c0392b; }
.pill.info { background: #eef2ff; color: #1d4ed8; }

.small { font-size: 8.6pt; color: #64748b; }
.chapter-lead {
  font-size: 9.6pt; color: #334155; background: #f8fafc; border: 0.6pt solid #e2e8f0;
  border-radius: 4pt; padding: 7pt 10pt; margin-bottom: 10pt; text-indent: 0;
}
"""


# --------------------------------------------------------------------------- 辅助
def img_b64(path: str | Path, width: str = "100%") -> str:
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return f'<img src="data:image/png;base64,{data}" style="width:{width}"/>'


def svg_block(svg: str, width: str = "100%") -> str:
    return f'<div style="width:{width};margin:0 auto">{svg}</div>'


def figure(inner: str, caption: str) -> str:
    return f'<div class="figure">{inner}<div class="cap">{caption}</div></div>'


def table(headers: list[str], rows: list[list[str]], caption: str = "", cls: str = "",
          widths: list[str] | None = None, num_cols: set[int] | None = None) -> str:
    num_cols = num_cols or set()
    out = [f'<table class="{cls}">']
    if caption:
        out.append(f"<caption>{caption}</caption>")
    if widths:
        out.append("<colgroup>" + "".join(f'<col style="width:{w}">' for w in widths) + "</colgroup>")
    out.append("<thead><tr>" + "".join(
        f'<th class="{"num" if i in num_cols else ""}">{h}</th>' for i, h in enumerate(headers)) + "</tr></thead><tbody>")
    for r in rows:
        out.append("<tr>" + "".join(
            f'<td class="{"num" if i in num_cols else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def note(title: str, body: str, kind: str = "") -> str:
    return f'<div class="note {kind}"><span class="t">{title}</span>{body}</div>'


def load_metrics() -> dict:
    path = C.RESULTS_DIR / "metrics.json"
    if not path.exists():
        raise SystemExit("未找到 results/metrics.json，请先运行 python main.py bench")
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- PDF
def _chrome() -> str:
    for p in CHROME_CANDIDATES:
        if Path(p).exists():
            return p
    raise SystemExit("未找到 Chrome/Edge，无法导出 PDF")


def html_to_pdf(html: str, out_pdf: Path, footer: str, title: str, name: str) -> Path:
    html_path = BUILD / f"{name}.html"
    html_path.write_text(html, encoding="utf-8")
    tmp_pdf = BUILD / f"{name}.raw.pdf"
    cmd = [
        _chrome(),
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-proxy-server",
        "--no-pdf-header-footer",
        "--disable-extensions",
        "--virtual-time-budget=6000",
        f"--print-to-pdf={tmp_pdf}",
        html_path.as_uri(),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    if not tmp_pdf.exists():
        raise SystemExit("PDF 生成失败")
    _stamp(tmp_pdf, out_pdf, footer, title)
    return out_pdf


def _cn_font() -> tuple[str, int | None]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for path, idx in CN_FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        try:
            if idx is None:
                pdfmetrics.registerFont(TTFont("CN", path))
            else:
                pdfmetrics.registerFont(TTFont("CN", path, subfontIndex=idx))
            return "CN", idx
        except Exception:  # noqa: BLE001
            continue
    return "Helvetica", None


def _stamp(src: Path, dst: Path, footer: str, title: str) -> None:
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas

    font, _ = _cn_font()
    reader = PdfReader(str(src))
    total = len(reader.pages)
    first = reader.pages[0]
    w = float(first.mediabox.width)
    h = float(first.mediabox.height)

    # 单个画布 + 单一字体嵌入：避免每页重复嵌入 CJK 字体导致体积膨胀
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    for i in range(total):
        if i > 0:
            c.setFont(font, 7.4)
            c.setFillColorRGB(0.42, 0.47, 0.55)
            c.drawString(42, 24, footer)
            c.drawRightString(w - 42, 24, f"第 {i + 1} 页 / 共 {total} 页")
            c.setStrokeColorRGB(0.87, 0.90, 0.94)
            c.setLineWidth(0.5)
            c.line(42, 36, w - 42, 36)
        c.showPage()
    c.save()

    overlay_reader = PdfReader(BytesIO(buf.getvalue()))
    for i, page in enumerate(reader.pages):
        page.merge_page(overlay_reader.pages[i])

    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    writer.add_metadata({
        "/Title": title,
        "/Author": "",
        "/Subject": "数元链 · 公共数据授权运营的数据元件确权、计量与自动分账系统",
        "/Keywords": "区块链; 数据要素; 数据元件; 授权策略; 自动分账; 鲁棒指纹; 同态加密",
        "/Creator": "数元链",
        "/Producer": "数元链",
    })
    try:
        writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    except Exception:  # noqa: BLE001
        pass
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as f:
        writer.write(f)


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import design_report
    import test_report

    m = load_metrics()
    d_html, d_title, d_footer = design_report.build(m)
    t_html, t_title, t_footer = test_report.build(m)

    outputs = []
    outputs.append(html_to_pdf(d_html, C.DELIVER_DIR / "设计报告数元链.pdf", d_footer, d_title, "design"))
    print("  设计报告已生成：", outputs[-1])
    outputs.append(html_to_pdf(t_html, C.DELIVER_DIR / "测试报告数元链.pdf", t_footer, t_title, "test"))
    print("  测试报告已生成：", outputs[-1])

    for p in outputs:
        print(f"  {p.name}  {p.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
