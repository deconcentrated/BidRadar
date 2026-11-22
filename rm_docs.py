#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

import rm_ingest as ing

SCHEMA = """
CREATE TABLE IF NOT EXISTS dokumenty(id TEXT PRIMARY KEY, nazwa TEXT, format TEXT, ocr INT, miejsc INT, projekt TEXT, ocid TEXT,
  metoda TEXT, pewnosc REAL, podstawa TEXT, fakty TEXT, wgrano TEXT);
CREATE TABLE IF NOT EXISTS fragmenty(dokument TEXT, kod TEXT, miejsce TEXT, cytat TEXT, uzasadnienie TEXT, pewnosc TEXT,
  status TEXT DEFAULT 'do zatwierdzenia');
CREATE TABLE IF NOT EXISTS weryfikacja(dokument TEXT, pole TEXT, dokument_wartosc TEXT, bzp_wartosc TEXT, wynik TEXT, komentarz TEXT, zrodlo TEXT);
CREATE TABLE IF NOT EXISTS roznice(dokument TEXT, czas TEXT, rodzaj TEXT, element TEXT, status TEXT, przed TEXT, po TEXT, zrodlo TEXT);
"""
CLOSING = re.compile(r"odbi[oó]r\w* końcow|protokół odbioru|protokol odbioru|zakończeni\w* (robót|realizacji)|wykonan\w* w całości|"
                     r"usunięto|usunięcie (wad|kolizji)|rozwiązan\w* problem", re.I)
FACT_LABEL = {"rodzaj": "rodzaj dokumentu", "umowa_nr": "numer umowy", "umowa_data": "data umowy", "wykonawca": "wykonawca",
              "termin_dotychczasowy": "dotychczasowy termin", "termin_nowy": "nowy termin", "kwota": "kwota", "podstawa": "podstawa prawna",
              "nip": "NIP zamawiającego", "numer_kpo": "numer projektu KPO", "numery_bzp": "numery ogłoszeń BZP", "zadanie": "nazwa zadania",
              "termin_mies_od_zawarcia": "termin (mies. od zawarcia)", "wydluzenie_mies": "wydłużenie terminu (mies.)"}


def read_docx(path: Path) -> list[tuple[str, str]]:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    out = []
    for i, p in enumerate(root.iter(ns + "p"), 1):
        t = "".join((n.text or "") if n.tag == ns + "t" else (" " if n.tag == ns + "tab" else "") for n in p.iter())
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            out.append((f"akapit {len(out) + 1}", t))
    return out


def read_pdf(path: Path) -> tuple[list[tuple[str, str]], bool]:
    if not shutil.which("pdftotext"):
        raise RuntimeError("Brak pdftotext – zainstaluj: sudo apt install poppler-utils")
    n = 1
    try:
        info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=30).stdout
        n = int(re.search(r"Pages:\s*(\d+)", info).group(1))
    except Exception:
        pass
    pages = []
    for i in range(1, n + 1):
        t = subprocess.run(["pdftotext", "-f", str(i), "-l", str(i), "-layout", str(path), "-"], capture_output=True, text=True, timeout=60).stdout
        pages.append((f"str. {i}", t))
    if sum(len(t.strip()) for _, t in pages) > 80 * n:
        return pages, False
    if not shutil.which("tesseract"):
        raise RuntimeError("PDF bez warstwy tekstowej (skan), a brak Tesseract – zainstaluj: sudo apt install tesseract-ocr tesseract-ocr-pol")
    langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
    lang = "pol" if "\npol" in langs else "eng"
    pages = []
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", "300", "-png", str(path), f"{td}/p"], check=True, timeout=300)
        for i, img in enumerate(sorted(Path(td).glob("p-*.png")), 1):
            t = subprocess.run(["tesseract", str(img), "-", "-l", lang], capture_output=True, text=True, timeout=300).stdout
            pages.append((f"str. {i} (OCR)", t))
    return pages, True


def read_any(path: Path) -> tuple[str, list[tuple[str, str]], bool]:
    ext = path.suffix.lower()
    if ext == ".docx":
        return "DOCX", read_docx(path), False
    if ext == ".pdf":
        pages, ocr = read_pdf(path)
        return ("PDF (skan, OCR)" if ocr else "PDF"), pages, ocr
    if ext in (".txt", ".md"):
        lines = [l.strip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        return "TXT", [(f"wiersz {i}", l) for i, l in enumerate(lines, 1)], False
    if ext == ".doc":
        for tool in (["antiword", "-w", "0"], ["catdoc", "-w"]):
            if shutil.which(tool[0]):
                t = subprocess.run(tool + [str(path)], capture_output=True, text=True, timeout=120).stdout
                paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
                if paras:
                    return "DOC", [(f"akapit {i}", p) for i, p in enumerate(paras, 1)], False
        office = shutil.which("soffice") or shutil.which("libreoffice")
        if office:
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run([office, "--headless", "--convert-to", "docx", "--outdir", tmp, str(path)], capture_output=True, timeout=180)
                conv = Path(tmp) / (path.stem + ".docx")
                if conv.exists():
                    return "DOC", read_docx(conv), False
        raise RuntimeError("Plik .doc (stary Word) wymaga narzędzia antiword albo LibreOffice: sudo apt install antiword. "
                           "Możesz też zapisać plik jako DOCX lub PDF.")
    if ext in (".odt", ".rtf", ".html", ".htm"):
        import rm_invest
        return rm_invest.read_text(path)
    raise RuntimeError(f"Nieobsługiwany format {ext}. Obsługiwane: DOCX, DOC, PDF (także skany), ODT, RTF, HTML, TXT.")


def norm_kpo(s: str) -> str:
    s = s.upper().replace(" ", "")
    s = re.sub(r"-[1L|]W\.", "-IW.", s)
    head, _, tail = s.partition("-IW.")
    return head + "-IW." + tail.replace("O", "0") if tail else s


def pl_date(s: str | None) -> str | None:
    if not s:
        return None
    m = re.match(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return m.group(0) if m else None


def add_months(iso: str, k: int) -> str:
    d = date.fromisoformat(iso)
    m = d.month - 1 + k
    y, m = d.year + m // 12, m % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date(y, m, day).isoformat()


def fold(s: str) -> str:
    return re.sub(r"[^a-z0-9ąćęłńóśźż ]", " ", (s or "").lower())


def money(s: str | None) -> float | None:
    if not s:
        return None
    return ing.money(s.replace(" ", "").replace("\xa0", ""))


DATE = r"(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}|\d{4}-\d{2}-\d{2})"


def facts(full: str) -> dict:
    t = re.sub(r"[ \t\xa0]+", " ", full)
    low = t.lower()
    f = {}
    f["rodzaj"] = ("protokół konieczności" if "protokół konieczności" in low or "protokol koniecznosci" in low
                   else "aneks do umowy" if re.search(r"\baneks\b", low) else
                   "pismo / wniosek" if re.search(r"zwraca się z prośbą|wnioskuje|z poważaniem", low) else "dokument")
    m = re.search(r"Umow[aęy]\s*(?:nr|numer)\s*:?\s*([\w./-]+)\s*z\s*dnia\s*:?\s*" + DATE, t, re.I) or \
        re.search(r"umowy\s*nr\s*:?\s*([\w./-]+)\s*z\s*dnia\s*:?\s*" + DATE, t, re.I | re.S)
    if m:
        f["umowa_nr"], f["umowa_data"] = m.group(1).rstrip(".,"), pl_date(m.group(2))
    m = re.search(r"Wykonawca\s*:\s*(.+)", t)
    if m:
        f["wykonawca"] = m.group(1).strip()[:120]
    m = re.search(r"(?:sporządzony\s+)?dnia\s+" + DATE, t, re.I) or re.search(r"\b" + DATE + r"\s*r?\.?", t)
    if m:
        f["data_dokumentu"] = pl_date(m.group(1))
    m = re.search(r"(?:dotychczasowy|pierwotny)\s+termin[^:\n]*:\s*" + DATE, t, re.I)
    if m:
        f["termin_dotychczasowy"] = pl_date(m.group(1))
    m = re.search(r"nowy\s+termin[^:\n]*:\s*" + DATE, t, re.I) or re.search(r"wydłużeni\w*\s+terminu[^.]{0,120}?do\s+" + DATE, t, re.I | re.S)
    if m:
        f["termin_nowy"] = pl_date(m.group(1))
    m = re.search(r"o\s+okres\s+(\d+)\s+miesi", low)
    if m:
        f["wydluzenie_mies"] = int(m.group(1))
    m = re.search(r"do\s+(\d+)\s+miesięcy\s+od\s+dnia\s+zawarcia", low)
    if m:
        f["termin_mies_od_zawarcia"] = int(m.group(1))
    m = re.search(r"(?:koszt|kwot\w*|wartoś\w*)[^\n:]{0,80}:?\s*([\d\s]{1,15},\d{2})\s*zł\s*(brutto|netto)?", t, re.I)
    if m:
        f["kwota"], f["kwota_rodzaj"] = money(m.group(1)), (m.group(2) or "").lower() or None
    m = re.search(r"art\.?\s*455\s*ust\.?\s*1\s*pkt\.?\s*(\d)", t, re.I)
    if m:
        f["podstawa"] = f"art. 455 ust. 1 pkt {m.group(1)} Pzp"
    kpo = re.findall(r"KPOD\s*\.?\s*\d{2}\s*\.\s*\d{2}\s*-\s*[I1lL|]W\s*\.\s*\d{2}\s*-\s*[\dO]{4}\s*/\s*\d{2}", t)
    if kpo:
        f["numer_kpo"] = norm_kpo(re.sub(r"\s+", "", kpo[0]))
    bzp = re.findall(r"\d{4}\s*/\s*BZP\s*\d{8}", t)
    if bzp:
        f["numery_bzp"] = sorted({ing.bzp_base(b) for b in bzp})
    nip = re.search(r"NIP[:\s]*([\d\- ]{10,13})", t)
    if nip:
        f["nip"] = re.sub(r"\D", "", nip.group(1))
    m = re.search(r"(?:Zadanie|Dotyczy)\s*:\s*[„\"]?(.+?)[”\"]?(?:\s+numer wniosku|\n|$)", t, re.I)
    if m:
        f["zadanie"] = m.group(1).strip(" „”\".")[:200]
    return f


RULES = [
    ("R02", ["stan techniczn", "po ich odkryciu", "wód gruntowych", "warunki gruntow", "uwarunkowania terenowe", "projektu sporządzonego kilkanaście lat",
             "nie były możliwe do przewidzenia", "nie można było przewidzieć"],
     "Stan istniejący lub warunki terenowe inne niż przyjęte w przygotowaniu inwestycji"),
    ("R03", ["kolizj", "uzgodnieniach z mieszkańcami", "modyfikowaniu tras", "nową zabudową", "posadowienie"],
     "Kolizje, dostęp do terenu lub uzgodnienia z właścicielami"),
    ("R07", ["robót dodatkowych", "roboty dodatkowe", "dodatkowe roboty", "dodatkowych robót"], "Roboty dodatkowe nieujęte w zakresie umowy"),
    ("R06", ["przesunięcia terminu", "wydłużeni", "przedłużeni", "opóźnia", "opóźnienie"], "Przesunięcie terminu realizacji"),
    ("R05", ["deklaracji przedstawionej przez inwestora", "na podstawie deklaracji"], "Ryzyko przerzucone na podstawie deklaracji jednej ze stron"),
    ("R15", ["efektu użytkowego", "osiągnięcia efektu"], "Opóźnione lub zagrożone osiągnięcie efektu projektu"),
    ("R04", ["nieterminowy przebieg realizacji tej inwestycji", "przyłącza do budowanej"], "Zależność od innej inwestycji lub podmiotu trzeciego"),
    ("R09", ["projektu sporządzonego kilkanaście lat", "nieistotnych odstępstw od zatwierdzonego projektu"], "Nieaktualna lub niepełna dokumentacja projektowa"),
    ("R13", ["regulaminu wyboru przedsięwzięć", "krajowego planu odbudowy"], "Warunki finansowania i terminy instytucji finansującej"),
]


def paragraphs(units: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out = []
    for loc, txt in units:
        if not loc.startswith("str."):
            out.append((loc, txt))
            continue
        for k, para in enumerate([p for p in re.split(r"\n\s*\n", txt) if p.strip()], 1):
            out.append((f"{loc}, akapit {k}", para))
    return out


def fragments(units: list[tuple[str, str]], ocr: bool) -> list[dict]:
    out, seen, per = [], set(), {}
    for loc, txt in paragraphs(units):
        flat = re.sub(r"\s+", " ", txt)
        for sen in re.split(r"(?<=[.;])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ–-])|\s[–-]\s", flat):
            sen = sen.strip(" –-")
            if len(sen) < 30:
                continue
            low = sen.lower()
            for kod, keys, why in RULES:
                hit = [k for k in keys if k in low]
                if not hit or (kod, sen[:60]) in seen or per.get(kod, 0) >= 2:
                    continue
                seen.add((kod, sen[:60]))
                per[kod] = per.get(kod, 0) + 1
                q = sen if len(sen) <= 360 else sen[:357] + "…"
                pew = "średnia" if len(hit) > 1 or kod in ("R07", "R03") else "niska"
                if ocr and pew == "średnia":
                    pew = "niska"
                out.append({"kod": kod, "miejsce": loc, "cytat": q, "slowa": hit,
                            "uzasadnienie": f"{why}. Sygnał: " + ", ".join(f"„{h}”" for h in hit) + (" · tekst z OCR" if ocr else ""), "pewnosc": pew})
    order = {"średnia": 0, "niska": 1}
    return sorted(out, key=lambda x: (order[x["pewnosc"]], x["kod"]))[:16]


def link(db: ing.Db, f: dict, full: str) -> dict:
    c = db.c
    projs = c.execute("SELECT id, lp, nazwa, beneficjent, nip, id_publiczny, miejscowosc FROM projekty").fetchall()
    if f.get("numer_kpo"):
        for pid, lp, nazwa, benef, nip, idp, miasto in projs:
            if idp and norm_kpo(idp) == f["numer_kpo"]:
                return {"projekt": pid, "metoda": "numer projektu KPO", "pewnosc": 1.0, "podstawa": f"numer {f['numer_kpo']} w treści dokumentu"}
    for b in f.get("numery_bzp", []):
        for pid, lp, nazwa, benef, nip, idp, miasto in projs:
            if ing.bzp_base(idp) == b:
                return {"projekt": pid, "metoda": "numer ogłoszenia BZP", "pewnosc": 1.0, "podstawa": f"numer {b} w treści dokumentu"}
    if f.get("umowa_data"):
        for pid, lp, nazwa, benef, nip, idp, miasto in projs:
            ocid = best_ocid(db, pid)
            if ocid and c.execute("SELECT 1 FROM umowy WHERE ocid=? AND data_zawarcia=?", (ocid, f["umowa_data"])).fetchone() \
                    and fold(miasto or benef).split()[0] in fold(full):
                return {"projekt": pid, "metoda": "data umowy + gmina", "pewnosc": 0.9, "podstawa": f"umowa z {f['umowa_data']} i nazwa gminy w treści"}
    best = None
    title = f.get("zadanie") or full[:600]
    for pid, lp, nazwa, benef, nip, idp, miasto in projs:
        sc = SequenceMatcher(None, fold(title), fold(nazwa)).ratio()
        gm = fold(re.sub(r"^(Gmina|Miasto)\s+", "", benef or "")).split(" – ")[0].strip()
        bonus = .15 if gm and gm in fold(full) else 0
        if f.get("nip") and f["nip"] == (nip or ""):
            bonus += .3
        if not best or sc + bonus > best[0]:
            best = (sc + bonus, pid, sc, bonus > 0)
    if best and best[0] >= .55:
        return {"projekt": best[1], "metoda": "nazwa zadania + gmina", "pewnosc": round(min(best[0], .95), 2),
                "podstawa": f"nazwa zadania podobna w {round(best[2] * 100)}%" + ("; nazwa gminy w treści" if best[3] else "")}
    return {"projekt": None, "metoda": "brak", "pewnosc": 0, "podstawa": "nie znaleziono identyfikatorów ani podobnej nazwy zadania"}


def best_ocid(db: ing.Db, pid: str) -> str | None:
    r = db.c.execute("SELECT ocid FROM powiazania WHERE projekt=? AND ocid IS NOT NULL ORDER BY "
                     "CASE status WHEN 'zatwierdzone' THEN 0 WHEN 'poprawne' THEN 1 ELSE 2 END, pewnosc DESC LIMIT 1", (pid,)).fetchone()
    return r[0] if r else None


def crosscheck(db: ing.Db, pid: str, f: dict) -> list[dict]:
    ocid = best_ocid(db, pid)
    if not ocid:
        return [{"pole": "postępowanie w BZP", "dokument_wartosc": "—", "bzp_wartosc": "brak powiązania", "wynik": "brak w BZP",
                 "komentarz": "Projekt nie ma jeszcze powiązanego postępowania – weryfikacja po zatwierdzeniu dopasowania.", "zrodlo": ""}]
    c = db.c
    u = c.execute("SELECT data_zawarcia, wykonawca, wartosc, wyplacono, termin_planowany, termin_wykonania, w_terminie, ogloszenie "
                  "FROM umowy WHERE ocid=? ORDER BY czesc", (ocid,)).fetchall()
    upd = c.execute("SELECT numer FROM ogloszenia WHERE ocid=? AND typ='AgreementUpdateNotice'", (ocid,)).fetchall()
    if not u:
        return [{"pole": "umowa w BZP", "dokument_wartosc": f.get("umowa_nr", "—"), "bzp_wartosc": "brak ogłoszenia o wykonaniu umowy",
                 "wynik": "brak w BZP", "komentarz": "Brak w kopii ≠ brak publikacji.", "zrodlo": ""}]
    sign, wyk, val, paid, plan, end, term, notice = u[-1]
    src = notice
    out = []

    def add(pole, dv, bv, ok, kom=""):
        out.append({"pole": pole, "dokument_wartosc": dv, "bzp_wartosc": bv, "wynik": ok, "komentarz": kom, "zrodlo": src})

    if f.get("umowa_data"):
        ok = f["umowa_data"] == sign
        kom = ""
        if not ok and sign:
            cn = c.execute("SELECT data_publikacji FROM ogloszenia WHERE ocid=? AND typ='ContractNotice'", (ocid,)).fetchone()
            if cn and f["umowa_data"] < cn[0][:10]:
                kom = f"Data z dokumentu jest wcześniejsza niż ogłoszenie o zamówieniu ({cn[0][:10]}) – prawdopodobny błąd w dokumencie."
        add("data zawarcia umowy", f["umowa_data"], sign or "—", "zgodne" if ok else "niezgodne", kom)
    if f.get("wykonawca") and wyk:
        r = SequenceMatcher(None, fold(f["wykonawca"]).split(" sp")[0].strip(), fold(wyk).split(" sp")[0].strip()).ratio()
        add("wykonawca", f["wykonawca"], wyk, "zgodne" if r >= .9 else "niezgodne",
            "" if r >= .9 else f"Nazwy podobne w {round(r * 100)}% – możliwa literówka; do potwierdzenia przez eksperta.")
    if f.get("termin_dotychczasowy"):
        add("termin pierwotny", f["termin_dotychczasowy"], plan or "—", "zgodne" if plan == f["termin_dotychczasowy"] else ("brak w BZP" if not plan else "niezgodne"))
    nowy = f.get("termin_nowy") or (add_months(sign, f["termin_mies_od_zawarcia"]) if sign and f.get("termin_mies_od_zawarcia") else None)
    if nowy and end:
        ok = end <= nowy
        add("termin po zmianie vs wykonanie", nowy, f"wykonano {end}" + (" (po pierwotnym terminie)" if term and term.lower().startswith("nie") else ""),
            "zgodne" if ok else "niezgodne", "Wykonanie mieści się w nowym terminie." if ok else "Wykonanie po terminie wskazanym w dokumencie.")
    if f.get("kwota") and val and paid:
        diff = round(paid - val, 2)
        ok = abs(diff - f["kwota"]) < 1
        add("kwota zmiany vs wypłata", f"{f['kwota']:,.2f} zł {f.get('kwota_rodzaj') or ''}".replace(",", " ").replace(".", ","),
            f"wypłacono {paid:,.2f} – umowa {val:,.2f} = {diff:,.2f} zł".replace(",", " ").replace(".", ","),
            "zgodne" if ok else "niezgodne", "Kwota z dokumentu równa różnicy wypłaty i wartości umowy w BZP." if ok else "")
    if f.get("podstawa", "").endswith("pkt 3 Pzp") or f.get("podstawa", "").endswith("pkt 4 Pzp"):
        add("ogłoszenie o zmianie umowy", f["podstawa"], ", ".join(x[0] for x in upd) if upd else "brak",
            "zgodne" if upd else "brak w BZP",
            "" if upd else "Zmiana na podstawie art. 455 ust. 1 pkt 3/4 wymaga ogłoszenia o zmianie umowy – brak w kopii BZP (brak w kopii ≠ brak publikacji).")
    notes = c.execute("SELECT uwagi FROM postepowania WHERE ocid=?", (ocid,)).fetchone()
    for n in json.loads((notes or [None])[0] or "[]"):
        add("jakość danych BZP", "—", n, "uwaga")
    return out


def process(db: ing.Db, path: Path, say=print) -> dict:
    db.c.executescript(SCHEMA)
    path = Path(path)
    fmt, units, ocr = read_any(path)
    full = "\n".join(t for _, t in units)
    f = facts(full)
    ln = link(db, f, full)
    frs = fragments(units, ocr)
    checks = crosscheck(db, ln["projekt"], f) if ln["projekt"] else []
    did = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    c = db.c
    before = snapshot(db, ln["projekt"], did) if ln["projekt"] else None
    for t in ("fragmenty", "weryfikacja", "roznice"):
        c.execute(f"DELETE FROM {t} WHERE dokument=?", (did,))
    c.execute("INSERT OR REPLACE INTO dokumenty VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              (did, path.name, fmt, int(ocr), len(units), ln["projekt"], best_ocid(db, ln["projekt"]) if ln["projekt"] else None,
               ln["metoda"], ln["pewnosc"], ln["podstawa"], json.dumps(f, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))
    for x in frs:
        c.execute("INSERT INTO fragmenty(dokument,kod,miejsce,cytat,uzasadnienie,pewnosc) VALUES(?,?,?,?,?,?)",
                  (did, x["kod"], x["miejsce"], x["cytat"], x["uzasadnienie"], x["pewnosc"]))
    for x in checks:
        c.execute("INSERT INTO weryfikacja VALUES(?,?,?,?,?,?,?)", (did, x["pole"], x["dokument_wartosc"], x["bzp_wartosc"], x["wynik"], x["komentarz"], x["zrodlo"]))
    if before is not None:
        stamp = datetime.now().isoformat(timespec="seconds")
        for r in diff(before, f, frs, full):
            c.execute("INSERT INTO roznice VALUES(?,?,?,?,?,?,?,?)", (did, stamp, r["rodzaj"], r["element"], r["status"], r["przed"], r["po"], r["zrodlo"]))
    c.commit()
    say(f"{path.name}: {fmt}, projekt {ln['projekt']} ({ln['metoda']}, {ln['pewnosc']}), {len(frs)} fragmentów, "
        f"weryfikacja: {', '.join(x['pole'] + '=' + x['wynik'] for x in checks)}")
    return {"id": did}


def snapshot(db: ing.Db, pid: str, skip_doc: str) -> dict:
    c = db.c
    fk, src = {}, {}
    for did, name, js in c.execute("SELECT id, nazwa, fakty FROM dokumenty WHERE projekt=? AND id<>? ORDER BY wgrano", (pid, skip_doc)):
        for k, v in (json.loads(js or "{}")).items():
            if v not in (None, "", [], {}):
                fk[k], src[k] = v, name
    risks = {}
    for did, name in c.execute("SELECT id, nazwa FROM dokumenty WHERE projekt=? AND id<>?", (pid, skip_doc)).fetchall():
        for (kod,) in c.execute("SELECT DISTINCT kod FROM fragmenty WHERE dokument=? AND status NOT LIKE 'odrzuc%'", (did,)):
            risks.setdefault(kod, f"dokument {name}")
    for kod, ogl in c.execute("SELECT kod, ogloszenie FROM zdarzenia WHERE projekt=? AND status NOT LIKE 'odrzuc%'", (pid,)):
        for k in (kod or "").split("/"):
            risks.setdefault(k, f"ogłoszenie BZP {ogl}")
    return {"fakty": fk, "zrodla": src, "ryzyka": risks}


def diff(before: dict, facts_now: dict, frags: list[dict], full: str) -> list[dict]:
    out = []
    for k, v in facts_now.items():
        if v in (None, "", [], {}) or k in ("rodzaj", "data_dokumentu", "kwota_rodzaj"):
            continue
        label = FACT_LABEL.get(k, k.replace("_", " "))
        old = before["fakty"].get(k)
        if old is None:
            out.append({"rodzaj": "fakt", "element": label, "status": "nowy", "przed": None, "po": str(v), "zrodlo": None})
        elif str(old).strip().lower() == str(v).strip().lower():
            out.append({"rodzaj": "fakt", "element": label, "status": "potwierdzony", "przed": str(old), "po": str(v), "zrodlo": before["zrodla"].get(k)})
        else:
            out.append({"rodzaj": "fakt", "element": label, "status": "zmieniony", "przed": str(old), "po": str(v), "zrodlo": before["zrodla"].get(k)})
    now_codes = {f["kod"] for f in frags}
    for kod in sorted(now_codes):
        st = "potwierdzone" if kod in before["ryzyka"] else "nowe"
        out.append({"rodzaj": "ryzyko", "element": kod, "status": st, "przed": before["ryzyka"].get(kod), "po": "wskazane w tym dokumencie", "zrodlo": before["ryzyka"].get(kod)})
    closing = CLOSING.search(full or "")
    for kod in sorted(set(before["ryzyka"]) - now_codes):
        out.append({"rodzaj": "ryzyko", "element": kod, "status": "zamknięte" if closing else "bez zmian",
                    "przed": before["ryzyka"][kod],
                    "po": f"dokument wskazuje zakończenie: „{closing.group(0)}”" if closing else "dokument nie wspomina",
                    "zrodlo": before["ryzyka"][kod]})
    return out


def listing(db: ing.Db) -> list[dict]:
    db.c.executescript(SCHEMA)
    c = db.c
    out = []
    for did, nazwa, fmt, ocr, miejsc, pid, ocid, met, pew, pod, fk, wg in c.execute("SELECT * FROM dokumenty ORDER BY wgrano").fetchall():
        lp = c.execute("SELECT lp FROM projekty WHERE id=?", (pid,)).fetchone() if pid else None
        out.append({"id": did, "nazwa": nazwa, "format": fmt, "ocr": bool(ocr), "miejsc": miejsc, "projekt": pid, "lp": lp[0] if lp else None,
                    "ocid": ocid, "powiazanie": {"metoda": met, "pewnosc": pew, "podstawa": pod}, "fakty": json.loads(fk or "{}"), "wgrano": wg,
                    "fragmenty": [dict(zip(("rowid", "kod", "miejsce", "cytat", "uzasadnienie", "pewnosc", "status"), r)) for r in
                                  c.execute("SELECT rowid,kod,miejsce,cytat,uzasadnienie,pewnosc,status FROM fragmenty WHERE dokument=?", (did,))],
                    "weryfikacja": [dict(zip(("pole", "dokument", "bzp", "wynik", "komentarz", "zrodlo"), r)) for r in
                                    c.execute("SELECT pole,dokument_wartosc,bzp_wartosc,wynik,komentarz,zrodlo FROM weryfikacja WHERE dokument=?", (did,))],
                    "roznice": [dict(zip(("rodzaj", "element", "status", "przed", "po", "zrodlo"), r)) for r in
                                c.execute("SELECT rodzaj,element,status,przed,po,zrodlo FROM roznice WHERE dokument=?", (did,))]})
    return out


if __name__ == "__main__":
    db = ing.Db(Path("bidradar.db" if Path("bidradar.db").exists() or not Path("risk_miner.db").exists() else "risk_miner.db"))
    for a in sys.argv[1:]:
        try:
            process(db, Path(a))
        except Exception as e:
            print(f"{a}: BŁĄD {e}")
