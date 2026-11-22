from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path

CSS = """
:root{--bg:#f5f9ee;--ink:#1b2410;--ink2:#535e47;--line:#e3ecd6;--brand:#8fd11f;--deep:#2d5a0e;--src:#1d5d99;--src50:#e7f1fb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 "Manrope",system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.page{max-width:900px;margin:32px auto;background:#fff;border:1px solid var(--line);border-radius:16px;padding:40px 48px}
header{display:flex;align-items:center;gap:16px;border-bottom:2px solid var(--brand);padding-bottom:16px;margin-bottom:24px}
.wm{font-weight:800;font-size:24px;line-height:1;letter-spacing:-.04em;color:var(--deep)} .wm span{color:#7dbb12}
header .meta{margin-left:auto;text-align:right;font-size:12px;color:var(--ink2)}
.bar{max-width:900px;margin:24px auto 0;display:flex;gap:10px;justify-content:flex-end}
.bar button{font-family:inherit;font-weight:600;font-size:14px;min-height:44px;padding:0 18px;border-radius:10px;border:1px solid var(--line);background:#fff;cursor:pointer}
.bar button.primary{background:var(--brand);border-color:var(--brand);color:#16220a}
.bar button:focus-visible{outline:2px solid #3d6b00;outline-offset:2px}
h1{font-size:26px;line-height:1.2;margin:0 0 12px;letter-spacing:-.02em}
h2{font-size:19px;margin:28px 0 10px;padding-top:10px;border-top:1px solid var(--line)}
h3{font-size:16px;margin:20px 0 8px} h4{font-size:15px;margin:16px 0 6px}
p{margin:8px 0} ul,ol{margin:8px 0;padding-left:22px} li{margin:3px 0}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:13px;page-break-inside:auto}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f6faef;font-weight:700} tr{page-break-inside:avoid}
code{font:600 .9em ui-monospace,Menlo,monospace;background:#eef1ef;padding:1px 5px;border-radius:4px}
blockquote{margin:10px 0;padding:8px 12px;background:var(--src50);border-left:3px solid var(--src);color:var(--ink2)}
a{color:var(--src)} hr{border:0;border-top:1px solid var(--line);margin:20px 0}
footer{margin-top:32px;padding-top:12px;border-top:1px solid var(--line);font-size:12px;color:var(--ink2)}
@media print{body{background:#fff}.page{border:0;margin:0;padding:0;max-width:none}.bar{display:none}a{color:inherit}
  @page{margin:16mm 14mm}}
@media (max-width:640px){.page{margin:0;border-radius:0;padding:24px 18px}header{flex-wrap:wrap}header .meta{margin-left:0;text-align:left}}
"""


def inline(s: str) -> str:
    s = html.escape(s, quote=False).replace("&lt;br&gt;", "<br>").replace("&lt;br/&gt;", "<br>")
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+|/[^)\s]*)\)", lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"(?<![\"'=])(https?://[^\s<]+)", r'<a href="\1">\1</a>', s)
    return s


def md_to_html(md: str) -> str:
    out, lines, i = [], md.splitlines(), 0
    para: list[str] = []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        ln = lines[i].rstrip()
        if not ln.strip():
            flush(); i += 1; continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            flush(); lvl = len(m.group(1)); out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>"); i += 1; continue
        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", ln):
            flush(); out.append("<hr>"); i += 1; continue
        if ln.lstrip().startswith("|"):
            flush(); rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(lines[i].strip().strip("|")); i += 1
            cells = [[c.strip() for c in re.split(r"(?<!\\)\|", r)] for r in rows]
            head = cells[0] if len(cells) > 1 and re.match(r"^[\s:\-|]+$", rows[1]) else None
            body = cells[2:] if head else cells
            t = "<table>"
            if head:
                t += "<thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead>"
            t += "<tbody>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table>"
            out.append(t); continue
        if re.match(r"^\s*([-*+]|\d+[.)])\s+", ln):
            flush(); ordered = bool(re.match(r"^\s*\d+[.)]", ln)); items = []
            while i < len(lines) and re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[i]):
                item = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", lines[i].rstrip()); i += 1
                while i < len(lines) and lines[i].startswith("  ") and lines[i].strip() and not re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[i]):
                    item += " " + lines[i].strip(); i += 1
                items.append(f"<li>{inline(item)}</li>")
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>{''.join(items)}</{tag}>"); continue
        if ln.startswith(">"):
            flush(); q = []
            while i < len(lines) and lines[i].startswith(">"):
                q.append(lines[i].lstrip("> ").rstrip()); i += 1
            out.append("<blockquote>" + inline(" ".join(q)) + "</blockquote>"); continue
        para.append(ln.strip()); i += 1
    flush()
    return "\n".join(out)


def page(md: str, title: str, subtitle: str = "") -> str:
    body = md_to_html(md)
    stamp = datetime.now().strftime("%d.%m.%Y, %H:%M")
    return f"""<!DOCTYPE html>
<html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} – BidRadar</title><style>{CSS}</style></head>
<body>
<div class="bar"><button type="button" onclick="history.length>1?history.back():window.close()">Wróć</button>
<button type="button" class="primary" onclick="window.print()">Zapisz jako PDF</button></div>
<main class="page">
<header><div class="wm">Bid<span>Radar</span></div><div class="meta">{html.escape(subtitle)}<br>Wygenerowano {stamp}</div></header>
{body}
<footer>Raport pomocniczy BidRadar. Wartości z dokumentów i BZP mają podane źródło (ogłoszenie, pole, cytat); obliczenia podają liczebność próby;
wnioski oznaczone jako propozycje wymagają zatwierdzenia przez eksperta.</footer>
</main></body></html>"""


def save_html(md_path: Path, title: str | None = None, subtitle: str = "") -> Path:
    md_path = Path(md_path)
    md = md_path.read_text(encoding="utf-8")
    m = re.search(r"^#\s+(.+)$", md, re.M)
    out = md_path.with_suffix(".html")
    out.write_text(page(md, title or (m.group(1) if m else md_path.stem), subtitle), encoding="utf-8")
    return out
