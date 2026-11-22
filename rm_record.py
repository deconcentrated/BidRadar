#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

import rm_ingest as ing
import rm_report as report
import rm_docx
import rm_benchmark as bm
import rm_docs as docs

COLS = ["Lp.", "Identyfikator rekordu", "Rodzaj identyfikatora odbiorcy", "Identyfikator odbiorcy", "Nazwa odbiorcy", "Typ odbiorcy", "Kraj",
        "Kod województwa (TERYT)", "Województwo", "Siedziba odbiorcy", "Nazwa przedsięwzięcia", "Zakres rekordu",
        "Rodzaj identyfikatora publicznego", "Identyfikator publiczny", "Obszar tematyczny", "Rodzaj zamówienia", "Reżim prawny",
        "Waluta rozliczeń", "Status przedsięwzięcia", "Źródło rekordu", "Data sporządzenia rekordu", "Wersja rekordu", "Adnotacja instytucji"]


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower().replace("ł", "l"))
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


ALIASES = {
    "Lp.": ["lp", "l p", "nr", "nr porzadkowy", "numer porzadkowy", "pozycja"],
    "Identyfikator rekordu": ["identyfikator rekordu", "id rekordu", "id", "identyfikator", "kod rekordu", "numer rekordu"],
    "Rodzaj identyfikatora odbiorcy": ["rodzaj identyfikatora odbiorcy", "typ identyfikatora odbiorcy"],
    "Identyfikator odbiorcy": ["identyfikator odbiorcy", "nip", "nip odbiorcy", "nip beneficjenta", "nip zamawiajacego", "nip wnioskodawcy", "regon"],
    "Nazwa odbiorcy": ["nazwa odbiorcy", "odbiorca", "beneficjent", "nazwa beneficjenta", "zamawiajacy", "nazwa zamawiajacego", "wnioskodawca", "inwestor", "jst", "gmina"],
    "Typ odbiorcy": ["typ odbiorcy", "rodzaj odbiorcy", "forma prawna"],
    "Kraj": ["kraj"],
    "Kod województwa (TERYT)": ["kod wojewodztwa teryt", "kod wojewodztwa", "teryt"],
    "Województwo": ["wojewodztwo", "woj"],
    "Siedziba odbiorcy": ["siedziba odbiorcy", "siedziba", "miejscowosc", "miasto", "miejscowosc siedziby", "lokalizacja"],
    "Nazwa przedsięwzięcia": ["nazwa przedsiewziecia", "nazwa projektu", "tytul projektu", "tytul", "nazwa zadania", "przedmiot zamowienia", "przedmiot", "nazwa inwestycji", "nazwa"],
    "Zakres rekordu": ["zakres rekordu", "zakres"],
    "Rodzaj identyfikatora publicznego": ["rodzaj identyfikatora publicznego", "typ identyfikatora publicznego", "rodzaj identyfikatora"],
    "Identyfikator publiczny": ["identyfikator publiczny", "numer ogloszenia", "numer ogloszenia bzp", "nr ogloszenia", "numer bzp", "ogloszenie bzp",
                                "numer projektu", "numer umowy o dofinansowanie", "nr projektu", "ocid", "numer kpo", "numer postepowania", "identyfikator postepowania"],
    "Obszar tematyczny": ["obszar tematyczny", "sektor", "obszar"],
    "Rodzaj zamówienia": ["rodzaj zamowienia"],
    "Reżim prawny": ["rezim prawny", "tryb", "podstawa prawna"],
    "Waluta rozliczeń": ["waluta rozliczen", "waluta"],
    "Status przedsięwzięcia": ["status przedsiewziecia", "status", "status projektu"],
    "Źródło rekordu": ["zrodlo rekordu", "zrodlo"],
    "Data sporządzenia rekordu": ["data sporzadzenia rekordu", "data sporzadzenia", "data rekordu", "data"],
    "Wersja rekordu": ["wersja rekordu", "wersja"],
    "Adnotacja instytucji": ["adnotacja instytucji", "adnotacja", "uwagi", "komentarz", "notatka"],
}
_EXACT = {a: k for k, al in ALIASES.items() for a in al + [fold(k)]}


def canon(key: str) -> str | None:
    f = fold(key)
    if not f:
        return None
    if f in _EXACT:
        return _EXACT[f]
    for k, al in ALIASES.items():
        if any(len(a) >= 5 and a in f for a in al + [fold(k)]):
            return k
    return None


def xlsx_grid(path: Path) -> list[list]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        sheet = "xl/worksheets/sheet1.xml"
        try:
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            rid = wb.find("m:sheets/m:sheet", ns).get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for r in rels:
                if r.get("Id") == rid:
                    t = r.get("Target").lstrip("/")
                    sheet = t if t.startswith("xl/") else "xl/" + t
        except Exception:
            pass
        if sheet not in names:
            sheet = sorted(n for n in names if n.startswith("xl/worksheets/sheet"))[0]
        root = ET.fromstring(z.read(sheet))
    grid = []
    for r in root.iter(f"{{{ns['m']}}}row"):
        row = {}
        for c in r.findall("m:c", ns):
            ref = c.get("r") or ""
            col = re.match(r"[A-Z]+", ref).group() if ref else None
            v = c.find("m:v", ns)
            if c.get("t") == "s" and v is not None:
                val = shared[int(v.text)]
            elif c.get("t") == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t"))
            else:
                val = v.text if v is not None else None
            idx = 0
            for ch in col or "A":
                idx = idx * 26 + ord(ch) - 64
            row[idx - 1] = val
        if row:
            grid.append([row.get(i) for i in range(max(row) + 1)])
    return grid


def text_pairs(lines: list[str]) -> list[list]:
    out = []
    for l in lines:
        m = re.match(r"\s*[-*•|]?\s*\**([^:|\t]{2,60}?)\**\s*[:|\t]\s*(.+?)\s*\|?\s*$", l)
        if m and canon(m.group(1)):
            out.append([m.group(1).strip(), m.group(2).strip()])
    return out


def raw_grid(path: Path) -> list[list]:
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        return xlsx_grid(path)
    if ext in (".csv", ".tsv", ".txt") and ext != ".txt" or (ext == ".txt" and path.read_text(encoding="utf-8", errors="replace").count(";") > 5):
        t = path.read_bytes()
        for enc in ("utf-8-sig", "cp1250", "iso-8859-2"):
            try:
                t = t.decode(enc)
                break
            except (UnicodeDecodeError, AttributeError):
                continue
        try:
            dialect = csv.Sniffer().sniff(t[:4000], delimiters=";,\t|")
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ";" if t.count(";") >= t.count(",") else ","
        return [r for r in csv.reader(io.StringIO(t), dialect) if any(x.strip() for x in r)]
    if ext == ".json":
        d = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(d, dict):
            for k in ("rekordy", "records", "projekty", "data", "items"):
                if isinstance(d.get(k), list):
                    d = d[k]
                    break
        rows = d if isinstance(d, list) else [d]
        keys = list(dict.fromkeys(k for r in rows for k in r))
        return [keys] + [[r.get(k) for k in keys] for r in rows]
    if ext in (".docx", ".pdf", ".md", ".txt", ".odt", ".rtf", ".html", ".htm"):
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                root = ET.fromstring(z.read("word/document.xml"))
            w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            rows = []
            for tr in root.iter(w + "tr"):
                rows.append(["".join(t.text or "" for t in tc.iter(w + "t")).strip() for tc in tr.findall(w + "tc")])
            if rows and len(rows) >= 2:
                if all(len(r) == 2 for r in rows) or (len(rows) > 3 and sum(canon(r[0]) is not None for r in rows if r) >= 3):
                    return [[r[0] for r in rows], [r[1] if len(r) > 1 else None for r in rows]]
                return rows
            lines = [t for _, t in docs.read_docx(path)]
        else:
            import rm_invest
            _, units, _ = rm_invest.read_text(path)
            lines = [l for _, t in units for l in t.splitlines()]
        pairs = text_pairs(lines)
        if ext == ".md":
            tab = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines if l.strip().startswith("|") and not re.match(r"\s*\|[\s:|-]+\|\s*$", l)]
            if len(tab) >= 2 and len(tab[0]) > 2:
                return tab
        if pairs:
            return [[p[0] for p in pairs], [p[1] for p in pairs]]
        return []
    raise RuntimeError(f"Nieobsługiwany format {ext}.")


def records_from_grid(grid: list[list]) -> tuple[list[dict], list[str]]:
    notes = []
    grid = [[("" if v is None else str(v).strip()) for v in r] for r in grid if r and any(v not in (None, "") for v in r)]
    if not grid:
        return [], ["Plik nie zawiera danych."]
    vert = sum(canon(r[0]) is not None for r in grid if r) >= max(3, len(grid) * .5) and max(len(r) for r in grid) <= 4
    if vert:
        cols = max(len(r) for r in grid)
        recs = []
        for j in range(1, cols):
            rec = {}
            for r in grid:
                k = canon(r[0])
                if k and j < len(r) and r[j] and k not in rec:
                    rec[k] = r[j]
            if rec:
                recs.append(rec)
        notes.append("Układ pionowy (pole | wartość).")
        return recs, notes
    best = max(range(min(15, len(grid))), key=lambda i: sum(canon(v) is not None for v in grid[i]))
    head = grid[best]
    if best:
        notes.append(f"Nagłówek w wierszu {best + 1}.")
    mapping = {}
    for j, h in enumerate(head):
        k = canon(h)
        if k and k not in mapping.values():
            mapping[j] = k
        elif h:
            notes.append(f"Pominięta kolumna „{h}” (nieznana nazwa pola).")
    recs = []
    for r in grid[best + 1:]:
        rec = {mapping[j]: v for j, v in enumerate(r) if j in mapping and v}
        if len(rec) >= 2:
            recs.append(rec)
    return recs, notes


def excel_date(v: str) -> str:
    try:
        f = float(v)
        if 20000 < f < 80000:
            return (date(1899, 12, 30) + timedelta(days=int(f))).isoformat()
    except (TypeError, ValueError):
        pass
    return v


TERYT = {"02": "dolnośląskie", "04": "kujawsko-pomorskie", "06": "lubelskie", "08": "lubuskie", "10": "łódzkie", "12": "małopolskie",
         "14": "mazowieckie", "16": "opolskie", "18": "podkarpackie", "20": "podlaskie", "22": "pomorskie", "24": "śląskie",
         "26": "świętokrzyskie", "28": "warmińsko-mazurskie", "30": "wielkopolskie", "32": "zachodniopomorskie"}


def normalize(rec: dict, i: int, db: ing.Db) -> tuple[dict, list[str]]:
    notes = []
    r = {k: rec.get(k) for k in COLS}
    blob = " ".join(str(v) for v in rec.values() if v)
    if r["Data sporządzenia rekordu"]:
        r["Data sporządzenia rekordu"] = excel_date(r["Data sporządzenia rekordu"])
    idp = r["Identyfikator publiczny"] or ""
    if not ing.bzp_base(idp) and not re.search(r"KPO|ocds-", idp, re.I):
        found = ing.bzp_base(blob) or (re.search(r"ocds-[0-9a-z]{6}-[0-9a-f-]{20,}", blob) or [None])[0]
        kpo = re.findall(r"KPOD\s*\.?\s*\d{2}\s*\.\s*\d{2}\s*-\s*[I1lL|]W\s*\.\s*\d{2}\s*-\s*[\dO]{4}\s*/\s*\d{2}", blob)
        found = found or (docs.norm_kpo(re.sub(r"\s+", "", kpo[0])) if kpo else None)
        if found:
            r["Identyfikator publiczny"] = found
            notes.append(f"Identyfikator publiczny odczytany z treści rekordu: {found}.")
    idp = r["Identyfikator publiczny"] or ""
    if not r["Rodzaj identyfikatora publicznego"]:
        r["Rodzaj identyfikatora publicznego"] = "Ogłoszenie BZP" if ing.bzp_base(idp) else "Numer projektu KPO" if "KPO" in idp.upper() else "OCID" if "ocds-" in idp else ""
    if not r["Województwo"] and r["Kod województwa (TERYT)"]:
        r["Województwo"] = TERYT.get(str(r["Kod województwa (TERYT)"]).zfill(2)[:2])
    if r["Województwo"]:
        r["Województwo"] = str(r["Województwo"]).lower().replace("województwo", "").replace("woj.", "").strip()
    if not r["Lp."]:
        mx = db.c.execute("SELECT MAX(lp) FROM projekty").fetchone()[0] or 0
        r["Lp."] = str(mx + i)
        notes.append(f"Brak Lp. – nadano {r['Lp.']}.")
    try:
        int(float(r["Lp."]))
    except (TypeError, ValueError):
        r["Lp."] = str((db.c.execute("SELECT MAX(lp) FROM projekty").fetchone()[0] or 0) + i)
    if not r["Identyfikator rekordu"]:
        r["Identyfikator rekordu"] = f"TEST-{datetime.now():%Y%m%d}-{int(float(r['Lp.'])):03d}"
        notes.append(f"Brak identyfikatora rekordu – nadano {r['Identyfikator rekordu']}.")
    for k in ("Nazwa odbiorcy", "Nazwa przedsięwzięcia", "Identyfikator odbiorcy", "Siedziba odbiorcy"):
        r[k] = r[k] or ""
    return r, notes


def fill_from_bzp(api: ing.Bzp, r: dict, notes: list[str]):
    num = ing.bzp_base(r["Identyfikator publiczny"])
    n = None
    if num:
        cn = api.get(NoticeType="ContractNotice", NoticeNumber=num)
        n = cn[0] if cn else None
        if not n:
            for t in ing.TYPES[1:]:
                x = api.get(NoticeType=t, NoticeNumber=num)
                if x:
                    n = x[0]
                    break
    if not n and not ing.digits(r["Identyfikator odbiorcy"]) and r["Nazwa odbiorcy"]:
        org_q = re.sub(r"^(Gmina|Miasto i Gmina|Miasto)\s+", "", r["Nazwa odbiorcy"], flags=re.I).strip()
        pool = api.all(NoticeType="ContractNotice", OrganizationName=org_q)
        ids = {}
        for x in pool:
            if fold(org_q) in fold(x.get("organizationName")):
                ids[x.get("organizationNationalId")] = ids.get(x.get("organizationNationalId"), 0) + 1
        if ids:
            nip = max(ids, key=ids.get)
            r["Identyfikator odbiorcy"] = nip
            notes.append(f"NIP uzupełniony z BZP ({nip}) – najczęstszy identyfikator zamawiającego „{org_q}” w ogłoszeniach; do potwierdzenia.")
    if n:
        if not ing.digits(r["Identyfikator odbiorcy"]):
            r["Identyfikator odbiorcy"] = n.get("organizationNationalId") or ""
            notes.append(f"NIP uzupełniony z ogłoszenia {n['noticeNumber']}.")
        if not r["Nazwa odbiorcy"]:
            r["Nazwa odbiorcy"] = n.get("organizationName") or ""
            notes.append(f"Nazwa odbiorcy uzupełniona z ogłoszenia {n['noticeNumber']}.")
        if not r["Siedziba odbiorcy"]:
            r["Siedziba odbiorcy"] = n.get("organizationCity") or ""
        if not r["Nazwa przedsięwzięcia"]:
            r["Nazwa przedsięwzięcia"] = n.get("orderObject") or ""
            notes.append(f"Nazwa przedsięwzięcia uzupełniona z ogłoszenia {n['noticeNumber']}.")
        if not r["Województwo"] and n.get("organizationProvince"):
            r["Województwo"] = bm.PROV.get(n["organizationProvince"], "")


def link(api: ing.Bzp, r: dict) -> list[dict]:
    idp = r["Identyfikator publiczny"] or ""
    m = re.search(r"ocds-[0-9a-z]{6}-[0-9a-f-]{20,}", idp)
    if m and not ing.bzp_base(idp):
        ocid = m.group(0)
        org_q = re.sub(r"^(Gmina|Miasto i Gmina|Miasto)\s+", "", r["Nazwa odbiorcy"], flags=re.I).strip()
        grp = ing.group_by_ocid(api, ocid, org_q, "2000-01-01T00:00:00")
        if grp:
            ok = ing.digits(grp[0].get("organizationNationalId")) == ing.digits(r["Identyfikator odbiorcy"])
            return [{"ocid": ocid, "metoda": "OCID wprost", "pewnosc": 1.0 if ok else .7, "status": "poprawne" if ok else "niepewne",
                     "podstawa": f"OCID z rekordu; {len(grp)} ogłoszeń; NIP {'zgodny' if ok else 'nie do potwierdzenia'}", "ogloszenia": grp, "braki": []}]
    rec = dict(r)
    rec["Identyfikator odbiorcy"] = r["Identyfikator odbiorcy"] or "0"
    return ing.link_project(api, rec)


def pctl_rank(vals: list[float], x: float) -> int | None:
    v = [a for a in vals if a is not None]
    return round(sum(a <= x for a in v) / len(v) * 100) if v and x is not None else None


def months_between(a: str | None, b: str | None) -> float | None:
    return bm.months(a, b)


def export_one(db: ing.Db, pid: str, notes: list[str], src: str) -> dict:
    c = db.c
    c.row_factory = __import__("sqlite3").Row
    p = dict(c.execute("SELECT * FROM projekty WHERE id=?", (pid,)).fetchone())
    links = [dict(x) for x in c.execute("SELECT * FROM powiazania WHERE projekt=? ORDER BY pewnosc DESC", (pid,))]
    best = next((l for l in links if l["status"] in ("zatwierdzone", "poprawne")), links[0] if links else None)
    out = {"rekord": p, "plik_zrodlowy": src, "uwagi_importu": notes, "eksport": datetime.now().isoformat(timespec="seconds"),
           "podsektor": bm.subsector(p["nazwa"]), "powiazania": links}
    if best and best["ocid"]:
        o = best["ocid"]
        out["postepowanie"] = dict(c.execute("SELECT * FROM postepowania WHERE ocid=?", (o,)).fetchone() or {})
        for k in ("braki", "uwagi"):
            try:
                out["postepowanie"][k] = json.loads(out["postepowanie"].get(k) or "[]")
            except Exception:
                pass
        out["ogloszenia"] = [dict(x) for x in c.execute("SELECT numer,typ,data_publikacji,url FROM ogloszenia WHERE ocid=? ORDER BY data_publikacji", (o,))]
        out["umowy"] = [dict(x) for x in c.execute("SELECT * FROM umowy WHERE ocid=? ORDER BY czesc", (o,))]
        out["zmiany_umowy"] = [dict(x) for x in c.execute("SELECT ogloszenie,podstawa,przyczyna FROM zmiany WHERE ocid=?", (o,))]
    else:
        out.update({"postepowanie": {}, "ogloszenia": [], "umowy": [], "zmiany_umowy": []})
    url = {x["numer"]: x["url"] for x in out["ogloszenia"]}
    c.row_factory = None
    out["zdarzenia_ryzyka"] = [dict(e, url=e.get("url") or url.get(e["ogloszenie"])) for e in ing.events_detail(db, pid)]
    out["poprawki_eksperta"] = [dict(zip(("typ", "pole", "wartosc", "kto", "czas"), r)) for r in c.execute(
        "SELECT typ,pole,wartosc,kto,czas FROM korekty WHERE (typ IN ('projekt','powiazanie') AND klucz=?) OR (typ='zdarzenie' AND klucz LIKE ?)",
        (pid, pid + "|%"))]

    U = out["umowy"]
    v = sum(u["wartosc"] or 0 for u in U) or None
    paid = sum(u["wyplacono"] or 0 for u in U) or None
    cost = round((paid - v) / v * 100, 1) if v and paid else None
    late = any((u["w_terminie"] or "").lower().startswith("nie") for u in U) if U else None
    bad = any((u["nalezycie"] or "").lower().startswith("nie") for u in U) if U else None
    plan = max((u["termin_planowany"] for u in U if u["termin_planowany"]), default=None)
    end = max((u["termin_wykonania"] for u in U if u["termin_wykonania"]), default=None)
    sign = min((u["data_zawarcia"] for u in U if u["data_zawarcia"]), default=None)
    delay = months_between(plan, end) if plan and end else None
    kpo = "kpo" in (p.get("rodzaj_id") or "").lower() and not (best and best["status"] == "zatwierdzone")
    if not U:
        stan = "brak danych o wykonaniu umowy w BZP" if best and best["ocid"] else "brak powiązania z BZP"
    elif bad or (cost is not None and cost > 15):
        stan = "ryzyko"
    elif late:
        stan = "opóźnienie"
    else:
        stan = "w normie"
    if best and best["status"] == "niepewne" and U:
        stan += " (wg niepewnego powiązania – do decyzji eksperta)"
    W = {"wartosc_umow": v, "wyplacono": paid, "zmiana_kosztu_pct": cost, "liczba_umow": len(U), "data_zawarcia": sign, "termin_planowany": plan,
         "termin_wykonania": end, "opoznienie_mies": delay if delay and delay > 0 else 0 if delay is not None else None,
         "po_terminie": late, "nienalezycie": bad, "liczba_zmian_umowy": sum(u["liczba_zmian"] or 0 for u in U) if U else None,
         "liczba_ofert": out["postepowanie"].get("liczba_ofert"), "ocena": stan, "status_powiazania": best["status"] if best else "brak",
         "do_decyzji_eksperta": bool(best and best["status"] == "niepewne") or kpo}
    try:
        rows = c.execute("SELECT zmiana_pct, opoznienie_mies, w_terminie FROM umowy_sektor WHERE podsektor=?", (out["podsektor"],)).fetchall()
    except Exception:
        rows = []
    if rows and U:
        W["porownanie"] = {"podsektor": out["podsektor"], "n": len(rows),
                           "zmiana_kosztu_percentyl": pctl_rank([r[0] for r in rows], cost) if cost is not None else None,
                           "mediana_zmiany_kosztu": bm.pctl([r[0] for r in rows], .5),
                           "udzial_po_terminie_w_grupie": round(sum(r[2] == 0 for r in rows) / max(1, sum(r[2] is not None for r in rows)) * 100)}
    out["wskazniki"] = W
    return out


def zl(v):
    return f"{v:,.2f} zł".replace(",", " ").replace(".", ",") if v is not None else "—"


def to_markdown(x: dict) -> str:
    p, W = x["rekord"], x["wskazniki"]
    L = [f"# Rekord projektu {p['id']} – {p['nazwa']}", "", f"*BidRadar · eksport {x['eksport']} · źródło: {x['plik_zrodlowy']} · dane z BZP, wnioski do weryfikacji eksperta*", "",
         "## 1. Rekord (dane wejściowe)", "", "| Pole | Wartość |", "|---|---|"]
    for k, n in (("lp", "Lp."), ("id", "Identyfikator rekordu"), ("beneficjent", "Nazwa odbiorcy"), ("nip", "Identyfikator odbiorcy (NIP)"),
                 ("wojewodztwo", "Województwo"), ("miejscowosc", "Siedziba odbiorcy"), ("nazwa", "Nazwa przedsięwzięcia"),
                 ("rodzaj_id", "Rodzaj identyfikatora publicznego"), ("id_publiczny", "Identyfikator publiczny"), ("adnotacja", "Adnotacja instytucji")):
        L.append(f"| {n} | {p.get(k) or '—'} |")
    L += [f"| Podsektor (MFiPR, z nazwy) | {x['podsektor']} |", ""]
    if x["uwagi_importu"]:
        L += ["Uwagi importu: " + " ".join(x["uwagi_importu"]), ""]
    L += ["## 2. Podsumowanie", "",
          f"- **Ocena (propozycja):** {W['ocena']}" + (" · **wymaga decyzji eksperta** (powiązanie niepewne)" if W["do_decyzji_eksperta"] else ""),
          f"- **Status powiązania z BZP:** {W['status_powiazania']}",
          f"- **Wartość umów:** {zl(W['wartosc_umow'])} · **wypłacono:** {zl(W['wyplacono'])} · **zmiana kosztu:** {bm.pl(W['zmiana_kosztu_pct']) + '%' if W['zmiana_kosztu_pct'] is not None else '—'}",
          f"- **Termin:** umowa {W['data_zawarcia'] or '—'} · planowany koniec {W['termin_planowany'] or '—'} · wykonanie {W['termin_wykonania'] or '—'}"
          + (f" · opóźnienie ok. {bm.pl(W['opoznienie_mies'])} mies." if W.get("opoznienie_mies") else "")
          + (" · **po terminie**" if W["po_terminie"] else " · w terminie" if W["po_terminie"] is False else ""),
          f"- **Należyte wykonanie:** {'nie' if W['nienalezycie'] else 'tak' if W['nienalezycie'] is False else '—'} · **liczba zmian umowy:** {W['liczba_zmian_umowy'] if W['liczba_zmian_umowy'] is not None else '—'} · **liczba ofert:** {W['liczba_ofert'] or '—'}"]
    if W.get("porownanie") and W["porownanie"].get("zmiana_kosztu_percentyl") is not None:
        cp = W["porownanie"]
        L.append(f"- **Na tle {cp['n']} zakończonych umów z podsektora „{cp['podsektor']}”:** zmiana kosztu wyższa lub równa niż w {cp['zmiana_kosztu_percentyl']}% umów "
                 f"(mediana {bm.pl(cp['mediana_zmiany_kosztu'])}%); po terminie kończy się {cp['udzial_po_terminie_w_grupie']}% umów w grupie.")
    L += ["", "## 3. Powiązanie z BZP", "", "| Status | Metoda | Pewność | OCID | Podstawa |", "|---|---|---|---|---|"]
    for l in x["powiazania"]:
        L.append(f"| {l['status']} | {l['metoda']} | {round((l['pewnosc'] or 0) * 100)}% | {l['ocid'] or '—'} | {(l['podstawa'] or '').replace('|', '/')} |")
    ps = x["postepowanie"]
    if ps:
        L += ["", f"Postępowanie: **{ps.get('przedmiot') or '—'}** · zamawiający {ps.get('zamawiajacy')} (NIP {ps.get('nip')}) · tryb {ps.get('tryb') or '—'} · CPV {ps.get('cpv') or '—'} · "
                  f"wartość szacunkowa {zl(ps.get('wartosc_szacunkowa'))} (netto) · ofert: {ps.get('liczba_ofert') or '—'}" + (f" · strona postępowania: {ps['strona_postepowania']}" if ps.get("strona_postepowania") else "")]
        for b in (ps.get("braki") or []) + (ps.get("uwagi") or []):
            L.append(f"- Uwaga o danych: {b}")
    L += ["", "## 4. Umowy (ogłoszenia o wykonaniu umowy)", ""]
    if x["umowy"]:
        L += ["| Ogłoszenie | Zawarcie | Wykonawca | Wartość (4.4) | Wypłacono (5.5) | Zmiana | Termin plan. (4.2) | Wykonanie (5.2) | W terminie (5.3) | Zmiany (5.4.1) | Należycie (5.6) |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for u in x["umowy"]:
            L.append(f"| {u['ogloszenie']} | {u['data_zawarcia'] or '—'} | {(u['wykonawca'] or '—').replace('|', '/')} | {zl(u['wartosc'])} | {zl(u['wyplacono'])} | "
                     f"{bm.pl(u['zmiana_kosztu_pct']) + '%' if u['zmiana_kosztu_pct'] is not None else '—'} | {u['termin_planowany'] or '—'} | {u['termin_wykonania'] or '—'} | "
                     f"{u['w_terminie'] or '—'} | {u['liczba_zmian'] if u['liczba_zmian'] is not None else '—'} | {u['nalezycie'] or '—'} |")
    else:
        L.append("Brak ogłoszenia o wykonaniu umowy w kopii BZP (brak w kopii ≠ brak publikacji; postępowanie może być w toku).")
    if x["zmiany_umowy"]:
        sp = lambda t, n: re.sub(r"\s+", " ", t or "")[:n]
        L += ["", "Zmiany umowy (ogłoszenia o zmianie umowy):", ""] + [f"- {z['ogloszenie']}: {sp(z['podstawa'], 160)} – {sp(z['przyczyna'], 300)}" for z in x["zmiany_umowy"]]
    L += ["", "## 5. Zdarzenia ryzyka (z reguł na polach ogłoszeń)", ""]
    if x["zdarzenia_ryzyka"]:
        L += ["| Kod | Opis | Ogłoszenie | Pole | Cytat | Status |", "|---|---|---|---|---|---|"]
        for e in x["zdarzenia_ryzyka"]:
            L.append(f"| {e['kod']} | {e['opis']} | [{e['ogloszenie']}]({e['url']}) | {e['pole']} | „{(e['cytat'] or '')[:160].replace('|', '/')}” | {e['status']} |")
        val = lambda f: f["wartosc"] if isinstance(f, dict) and f.get("wartosc") not in (None, "") else None
        L += ["", "Przyczyny, skutki i działania zaradcze (BZP – automatycznie; „ekspert” – poprawione w panelu):", "",
              "| Kod | Przyczyna | Skutek: dni | Skutek: zł | Działanie zaradcze | Skuteczność | Źródło |", "|---|---|---|---|---|---|---|"]
        for e in x["zdarzenia_ryzyka"]:
            src = sorted({(e[k] or {}).get("zrodlo", "").split(":")[0] for k in ("przyczyna", "skutek_dni", "skutek_zl", "dzialanie", "skutecznosc") if val(e.get(k))})
            kwota = val(e.get("skutek_zl"))
            L.append(f"| {e['kod']} | {(val(e.get('przyczyna')) or '—')[:200].replace('|', '/')} | {val(e.get('skutek_dni')) or '—'} | "
                     f"{fmt_pln(float(kwota)) if kwota is not None else '—'} | {(val(e.get('dzialanie')) or '—')[:160].replace('|', '/')} | "
                     f"{val(e.get('skutecznosc')) or '—'} | {', '.join(src) or '—'} |")
    else:
        L.append("Brak zdarzeń ryzyka w danych BZP.")
    if x.get("poprawki_eksperta"):
        L += ["", "Poprawki eksperta (przetrwają ponowny import):", ""] + [
            f"- {k['czas'][:16].replace('T', ' ')} · {k['kto']} · {k['typ']}: {k['pole']} = {str(k['wartosc'])[:120]}" for k in x["poprawki_eksperta"]]
    L += ["", "## 6. Dziennik ogłoszeń", "", "| Data | Typ | Numer | Link |", "|---|---|---|---|"]
    for n in x["ogloszenia"]:
        L.append(f"| {n['data_publikacji'][:10]} | {ing.TYPE_PL.get(n['typ'], n['typ'])} | {n['numer']} | {n['url']} |")
    L += ["", "## 7. Metoda", "",
          "- Łączenie: numer BZP → ogłoszenie o zamówieniu → OCID → wszystkie ogłoszenia postępowania (z kontrolą NIP); gdy brak ogłoszenia w kopii – odwołania w późniejszych ogłoszeniach; bez numeru BZP – dopasowanie rozmyte NIP + nazwa (zawsze do decyzji eksperta).",
          "- Pola: 4.1 data zawarcia, 4.2 okres realizacji, 4.3.1 wykonawca, 4.4 wartość umowy, 5.2 termin wykonania, 5.3 w terminie, 5.4.1 liczba zmian, 5.5 wypłacono, 5.6 należyte wykonanie; zduplikowane ogłoszenia o wykonaniu tej samej umowy liczone raz.",
          "- Kwoty z ogłoszeń o wykonaniu umowy są brutto; wartość szacunkowa zamówienia jest netto.", ""]
    return "\n".join(L)


def fmt_pln(v: float) -> str:
    return f"{v:+,.2f} zł".replace(",", " ").replace(".", ",")


FLAT = ["id", "lp", "beneficjent", "nip", "wojewodztwo", "miejscowosc", "nazwa", "id_publiczny", "podsektor", "status_powiazania", "ocid", "metoda", "pewnosc",
        "wartosc_umow", "wyplacono", "zmiana_kosztu_pct", "data_zawarcia", "termin_planowany", "termin_wykonania", "opoznienie_mies", "po_terminie",
        "nienalezycie", "liczba_zmian_umowy", "liczba_ofert", "ocena", "zdarzenia", "ogloszenia"]


def flat_row(x: dict) -> dict:
    p, W = x["rekord"], x["wskazniki"]
    l = next((q for q in x["powiazania"] if q["status"] in ("zatwierdzone", "poprawne")), x["powiazania"][0] if x["powiazania"] else {})
    return {**{k: p.get(k) for k in FLAT if k in p}, **{k: W.get(k) for k in FLAT if k in W}, "podsektor": x["podsektor"],
            "ocid": l.get("ocid"), "metoda": l.get("metoda"), "pewnosc": l.get("pewnosc"),
            "zdarzenia": " | ".join(f"{e['kod']}: {e['opis']} ({e['ogloszenie']})" for e in x["zdarzenia_ryzyka"]),
            "ogloszenia": " | ".join(f"{n['numer']} {n['url']}" for n in x["ogloszenia"])}


def save(x: dict, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w-]+", "_", x["rekord"]["id"])
    base = out / f"rekord_{safe}"
    md, js, cs = base.with_suffix(".md"), base.with_suffix(".json"), base.with_suffix(".csv")
    md.write_text(to_markdown(x), encoding="utf-8")
    js.write_text(json.dumps(x, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    row = flat_row(x)
    with open(cs, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FLAT, delimiter=";")
        w.writeheader()
        w.writerow(row)
    h = report.save_html(md, subtitle=f"Rekord projektu {x['rekord']['id']}")
    dx = rm_docx.save_docx(md, f"Rekord projektu {x['rekord']['id']}")
    return {"md": str(md), "json": str(js), "csv": str(cs), "html": str(h), "docx": str(dx)}


def export_many(db: ing.Db, ids: list[str], out: Path, fmt: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    xs = [export_one(db, i, [], "baza BidRadar") for i in ids if db.c.execute("SELECT 1 FROM projekty WHERE id=?", (i,)).fetchone()]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    f = out / f"rekordy_projektow_{stamp}.{'md' if fmt in ('html', 'docx') else fmt}"
    if fmt in ("md", "html", "docx"):
        head = [f"# BidRadar – rekordy projektów ({len(xs)})", "", f"*Eksport {datetime.now().isoformat(timespec='seconds')} · dane z BZP, wnioski do weryfikacji eksperta*", "",
                "| Lp. | Odbiorca | Przedsięwzięcie | Powiązanie | Zmiana kosztu | Ocena |", "|---|---|---|---|---|---|"]
        for x in xs:
            W = x["wskazniki"]
            head.append(f"| {x['rekord']['lp']} | {x['rekord']['beneficjent']} | {(x['rekord']['nazwa'] or '')[:80]} | {W['status_powiazania']} | "
                        f"{bm.pl(W['zmiana_kosztu_pct']) + '%' if W['zmiana_kosztu_pct'] is not None else '—'} | {W['ocena']} |")
        body = "\n\n---\n\n".join(re.sub(r"(?m)^#", "##", to_markdown(x)) for x in xs)
        f.write_text("\n".join(head) + "\n\n---\n\n" + body, encoding="utf-8")
        if fmt == "html":
            f = report.save_html(f, subtitle=f"Rekordy projektów ({len(xs)})")
        elif fmt == "docx":
            f = rm_docx.save_docx(f, f"Rekordy projektów ({len(xs)})")
    elif fmt == "json":
        f.write_text(json.dumps(xs, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    else:
        with open(f, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=FLAT, delimiter=";")
            w.writeheader()
            for x in xs:
                w.writerow(flat_row(x))
    return f


def process(path: Path, db: ing.Db, api: ing.Bzp, out: Path, say=print) -> list[dict]:
    grid = raw_grid(path)
    recs, notes0 = records_from_grid(grid)
    if not recs:
        raise RuntimeError(f"Nie rozpoznano rekordu w pliku {path.name}. Oczekiwane pola m.in.: Nazwa odbiorcy, Identyfikator odbiorcy (NIP), "
                           "Nazwa przedsięwzięcia, Identyfikator publiczny.")
    if not api.check():
        raise RuntimeError(f"Brak odpowiedzi z API BZP pod {api.base}: {api.last_error}. Uruchom kopię BZP (bzp_mirror.py).")
    ing.NOW = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    results = []
    for i, raw in enumerate(recs, 1):
        r, notes = normalize(raw, i, db)
        notes = notes0 + notes
        fill_from_bzp(api, r, notes)
        pid = r["Identyfikator rekordu"]
        say(f"Rekord {pid}: {r['Nazwa odbiorcy']} · {r['Identyfikator publiczny'] or 'bez identyfikatora publicznego'}")
        db.put("projekty", {"id": pid, "lp": int(float(r["Lp."])), "nazwa": r["Nazwa przedsięwzięcia"], "beneficjent": r["Nazwa odbiorcy"],
                            "nip": ing.digits(r["Identyfikator odbiorcy"]), "wojewodztwo": r["Województwo"], "miejscowosc": r["Siedziba odbiorcy"],
                            "id_publiczny": r["Identyfikator publiczny"], "rodzaj_id": r["Rodzaj identyfikatora publicznego"],
                            "adnotacja": r.get("Adnotacja instytucji"), "pobrano": ing.NOW})
        db.clear_project(pid)
        links = link(api, r)
        for l in links:
            db.put("powiazania", {"projekt": pid, "ocid": l["ocid"], "metoda": l["metoda"], "pewnosc": l["pewnosc"], "status": l["status"],
                                  "podstawa": l["podstawa"], "pobrano": ing.NOW}, replace=False)
            ing.analyse(pid, l, db)
            if l.get("braki"):
                notes += l["braki"]
        db.c.commit()
        x = export_one(db, pid, notes, path.name)
        files = save(x, out)
        say(f"  powiązanie: {links[0]['status']} ({links[0]['metoda']}) · ocena: {x['wskazniki']['ocena']} · zdarzenia: {len(x['zdarzenia_ryzyka'])}")
        say(f"  eksport: {files['md']}")
        results.append({"projekt": pid, "pliki": files, "wynik": x})
    return results


def main():
    ap = argparse.ArgumentParser(description="BidRadar – import i eksport pojedynczego rekordu projektu")
    ap.add_argument("plik", type=Path)
    ap.add_argument("--url", default=os.environ.get("RM_BZP_URL", "http://127.0.0.1:8010"))
    ap.add_argument("--db", type=Path, default=Path(os.environ.get("RM_DB", "bidradar.db")))
    ap.add_argument("--out", type=Path, default=Path("wyniki"))
    a = ap.parse_args()
    db = ing.Db(a.db)
    api = ing.Bzp(a.url, lambda op, adr, wyn: db.put("log", {"czas": datetime.now().isoformat(timespec="seconds"), "operacja": op, "adres": adr, "wynik": str(wyn)}, replace=False))
    try:
        process(a.plik, db, api, a.out)
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
