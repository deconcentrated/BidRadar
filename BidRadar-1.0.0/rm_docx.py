#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {W}>
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="22"/><w:lang w:val="pl-PL"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:pPr><w:spacing w:after="200"/></w:pPr><w:rPr><w:b/><w:color w:val="14532D"/><w:sz w:val="36"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:keepNext/><w:spacing w:before="280" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="14532D"/><w:sz w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="1F2937"/><w:sz w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="23"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:pPr><w:ind w:left="360"/><w:pBdr><w:left w:val="single" w:sz="12" w:space="8" w:color="16A34A"/></w:pBdr></w:pPr><w:rPr><w:color w:val="374151"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:pPr><w:ind w:left="360" w:hanging="240"/><w:spacing w:after="60"/></w:pPr></w:style>
<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="15803D"/><w:u w:val="single"/></w:rPr></w:style>
<w:style w:type="table" w:styleId="Tabela"><w:name w:val="Tabela BidRadar"/><w:tblPr><w:tblBorders>
<w:top w:val="single" w:sz="4" w:color="D1D5DB"/><w:bottom w:val="single" w:sz="4" w:color="D1D5DB"/><w:insideH w:val="single" w:sz="4" w:color="E5E7EB"/>
</w:tblBorders><w:tblCellMar><w:top w:w="40" w:type="dxa"/><w:left w:w="80" w:type="dxa"/><w:bottom w:w="40" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
</w:styles>"""

CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

LINK_RPR = '<w:rStyle w:val="Hyperlink"/>'
INLINE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|(?<![\w*])\*(?!\s)([^*]+?)\*(?![\w*])")


def _t(text: str, rpr: str = "") -> str:
    return f'<w:r>{"<w:rPr>" + rpr + "</w:rPr>" if rpr else ""}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


class Doc:
    def __init__(self):
        self.body: list[str] = []
        self.links: list[str] = []

    def runs(self, text: str, bold: bool = False) -> str:
        out, pos = [], 0
        base = "<w:b/>" if bold else ""
        for m in INLINE.finditer(text):
            if m.start() > pos:
                out.append(_t(text[pos:m.start()], base))
            if m.group(1) is not None:
                out.append(_t(m.group(1), "<w:b/>"))
            elif m.group(5) is not None:
                out.append(_t(m.group(5), base + '<w:i/><w:color w:val="4B5563"/>'))
            elif m.group(2) is not None:
                out.append(_t(m.group(2), base + '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="20"/>'))
            else:
                url = m.group(4)
                if url.startswith(("http://", "https://")):
                    self.links.append(url)
                    rid = f"rIdL{len(self.links)}"
                    out.append(f'<w:hyperlink r:id="{rid}">{_t(m.group(3), LINK_RPR)}</w:hyperlink>')
                else:
                    out.append(_t(m.group(3), base))
            pos = m.end()
        if pos < len(text):
            out.append(_t(text[pos:], base))
        return "".join(out)

    def para(self, text: str, style: str = "", bold: bool = False, prefix: str = ""):
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        self.body.append(f"<w:p>{ppr}{_t(prefix) if prefix else ''}{self.runs(text, bold)}</w:p>")

    def table(self, rows: list[list[str]]):
        n = max(len(r) for r in rows)
        width = 9600 // n
        x = ['<w:tbl><w:tblPr><w:tblStyle w:val="Tabela"/><w:tblW w:w="5000" w:type="pct"/></w:tblPr><w:tblGrid>'
             + "".join(f'<w:gridCol w:w="{width}"/>' for _ in range(n)) + "</w:tblGrid>"]
        for i, r in enumerate(rows):
            r = r + [""] * (n - len(r))
            head = i == 0
            x.append("<w:tr>" + ("<w:trPr><w:tblHeader/></w:trPr>" if head else ""))
            for c in r:
                shade = '<w:shd w:val="clear" w:color="auto" w:fill="F0FDF4"/>' if head else ""
                x.append(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}</w:tcPr>'
                         f'<w:p><w:pPr><w:spacing w:after="0"/></w:pPr>{self.runs(c.strip(), head)}</w:p></w:tc>')
            x.append("</w:tr>")
        x.append("</w:tbl>")
        self.body.append("".join(x))
        self.body.append('<w:p><w:pPr><w:spacing w:after="60"/></w:pPr></w:p>')


def _cells(line: str) -> list[str]:
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") else s
    return [c.replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def md_to_docx(md: str, out: Path, title: str = "BidRadar") -> Path:
    d = Doc()
    lines = md.splitlines()
    i, first = 0, True
    while i < len(lines):
        ln = lines[i].rstrip()
        if not ln.strip():
            i += 1
            continue
        if ln.lstrip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", lines[i]):
                    rows.append(_cells(lines[i]))
                i += 1
            if rows:
                d.table(rows)
            continue
        m = re.match(r"(#{1,4})\s+(.*)", ln)
        if m:
            lvl = len(m.group(1))
            d.para(m.group(2), "Title" if lvl == 1 and first else f"Heading{min(lvl if lvl > 1 else 1, 3)}")
            first = False
        elif re.match(r"\s*[-*]\s+", ln):
            d.para(re.sub(r"^\s*[-*]\s+", "", ln), "ListBullet", prefix="• ")
        elif ln.startswith(">"):
            d.para(ln.lstrip("> ").strip(), "Quote")
        elif re.fullmatch(r"-{3,}|\*{3,}", ln.strip()):
            pass
        else:
            d.para(ln.strip())
        i += 1
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>' + "".join(d.body) +
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
           'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdS" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>' +
            "".join(f'<Relationship Id="rIdL{k}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                    f'Target="{escape(u, {chr(34): "&quot;"})}" TargetMode="External"/>' for k, u in enumerate(d.links, 1)) + "</Relationships>")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(title)}</dc:title><dc:creator>BidRadar</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{ts}</dcterms:created></cp:coreProperties>')
    out = Path(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("docProps/core.xml", core)
    return out


def save_docx(md_path: str | Path, title: str | None = None) -> Path:
    p = Path(md_path)
    return md_to_docx(p.read_text(encoding="utf-8"), p.with_suffix(".docx"), title or p.stem.replace("_", " "))


if __name__ == "__main__":
    for a in sys.argv[1:]:
        print(save_docx(a))
