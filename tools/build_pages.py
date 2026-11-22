#!/usr/bin/env python3
import re
from pathlib import Path

T = Path(__file__).resolve().parent
ROOT = T.parent
U = (ROOT / "bidradar_ui.html").read_text(encoding="utf-8")
fonts = next(m.group(1) for m in re.finditer(r"<style>(\s*(?:/\*[^*]*\*/\s*)?@font-face.*?)</style>", U, re.S) if "Manrope" in m.group(1))

vals = {"FONTS": fonts}

for src, dst in (("landing.src.html", "landing.html"), ("login.src.html", "login.html")):
    s = (T / src).read_text(encoding="utf-8")
    for k, v in vals.items():
        s = s.replace("{{" + k + "}}", v)
    assert "{{" not in s, f"niepodstawione pole w {src}"
    (ROOT / dst).write_text(s, encoding="utf-8")
    print(f"{dst}: {len(s) // 1024} KB")

ui_path = ROOT / "bidradar_ui.html"
ui = ui_path.read_text(encoding="utf-8")
addon = (T / "ui_server_addon.html").read_text(encoding="utf-8")
A, B = '<div id="addon-start" hidden></div>', '<div id="addon-end" hidden></div>'
block = f"{A}\n{addon}{B}\n"
ui = re.sub(re.escape(A) + r".*?" + re.escape(B) + r"\n?", lambda m: block, ui, flags=re.S) if A in ui else ui.replace("</body>", block + "</body>", 1)
ui_path.write_text(ui, encoding="utf-8")
print(f"bidradar_ui.html: dodatek serwerowy {len(addon) // 1024} KB")
