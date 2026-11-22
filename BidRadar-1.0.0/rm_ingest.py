#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

TYPES = ["ContractNotice", "TenderResultNotice", "AgreementUpdateNotice", "ContractPerformingNotice"]
TYPE_PL = {"ContractNotice": "ogłoszenie o zamówieniu", "TenderResultNotice": "ogłoszenie o wyniku",
           "AgreementUpdateNotice": "ogłoszenie o zmianie umowy", "ContractPerformingNotice": "ogłoszenie o wykonaniu umowy"}
NOTICE_URL = "https://ezamowienia.gov.pl/mo-client-board/bzp/notice-details/id/{}"
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_xlsx(path: Path) -> list[dict]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for r in root.iter(f"{{{ns['m']}}}row"):
        row = {}
        for c in r.findall("m:c", ns):
            col = re.match(r"[A-Z]+", c.get("r")).group()
            v = c.find("m:v", ns)
            if c.get("t") == "s" and v is not None:
                val = shared[int(v.text)]
            elif c.get("t") == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t"))
            else:
                val = v.text if v is not None else None
            row[col] = val
        rows.append(row)
    head = rows[0]
    cols = sorted(head, key=lambda k: (len(k), k))
    return [{head[k]: r.get(k) for k in cols} for r in rows[1:] if any(r.values())]


class Bzp:
    def __init__(self, base: str, log):
        self.base = base.rstrip("/") + "/mo-board/api/v1/notice"
        self.log = log
        self.cache: dict[str, list] = {}
        self.last_error = ""
        host = urllib.parse.urlparse(self.base).hostname or ""
        handlers = [urllib.request.ProxyHandler({})] if host in ("127.0.0.1", "localhost", "::1") else []
        self.opener = urllib.request.build_opener(*handlers)

    def check(self) -> bool:
        url = self.base + "?" + urllib.parse.urlencode({"NoticeType": "ContractNotice", "PageSize": 1})
        try:
            with self.opener.open(url, timeout=30) as r:
                data = json.load(r)
            return isinstance(data, list)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return False

    def get(self, **params) -> list[dict]:
        url = self.base + "?" + urllib.parse.urlencode(params)
        if url in self.cache:
            return self.cache[url]
        for attempt in range(4):
            try:
                with self.opener.open(url, timeout=120) as r:
                    data = json.load(r)
                self.log("GET", url, len(data))
                self.cache[url] = data
                return data
            except Exception as e:
                self.log("BŁĄD", url, str(e))
                time.sleep(1.5 * (attempt + 1))
        return []

    def all(self, **params) -> list[dict]:
        out, after = [], None
        while True:
            p = dict(params, PageSize=100)
            if after:
                p["SearchAfter"] = after
            page = self.get(**p)
            out += page
            if len(page) < 100:
                return out
            after = page[-1]["objectId"]


def text(h: str) -> str:
    h = re.sub(r"<style.*?</style>", "", h or "", flags=re.S)
    h = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", h)
    h = html.unescape(re.sub(r"<[^>]+>", "", h))
    return re.sub(r"[ \t\xa0]+", " ", re.sub(r"\n\s*\n+", "\n", h))


def field(pattern: str, s: str) -> str:
    m = re.search(pattern, s, re.S)
    return m.group(1).strip() if m else ""


def money(v: str) -> float | None:
    v = re.sub(r"[^\d,\.]", "", v or "").replace(",", ".")
    if v.count(".") > 1:
        v = v.replace(".", "", v.count(".") - 1)
    try:
        return float(v)
    except ValueError:
        return None


def bzp_base(n: str | None) -> str | None:
    m = re.search(r"(\d{4})\s*/\s*BZP\s*0*(\d+)", n or "")
    return f"{m.group(1)}/BZP {int(m.group(2)):08d}" if m else None


def plan_date(s: str, sign: str | None) -> str:
    d = field(r"4\.2\.\) Okres realizacji[^:]*:.{0,40}?do\s*(\d{4}-\d{2}-\d{2})", s)
    if d or not sign:
        return d
    m = re.search(r"4\.2\.\) Okres realizacji[^:]*:\s*(\d+)\s*(miesi|mies|dni|dzień|tydz|tygodn|lat|rok)", s)
    if not m:
        return ""
    from datetime import date as _d, timedelta as _t
    try:
        d0, k, u = _d.fromisoformat(sign[:10]), int(m.group(1)), m.group(2)
    except ValueError:
        return ""
    if u.startswith(("dni", "dzień")):
        return (d0 + _t(days=k)).isoformat()
    if u.startswith(("tydz", "tygodn")):
        return (d0 + _t(weeks=k)).isoformat()
    mm = k * 12 if u.startswith(("lat", "rok")) else k
    y, mo = divmod(d0.month - 1 + mm, 12)
    y, mo = d0.year + y, mo + 1
    day = min(d0.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1])
    return _d(y, mo, day).isoformat()


def digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def quote(s: str, label: str, n: int = 160) -> str:
    i = s.find(label)
    return re.sub(r"\s+", " ", s[i:i + n]).strip() if i >= 0 else ""


def norm_words(s: str) -> set[str]:
    stop = {"w", "i", "na", "do", "z", "oraz", "dla", "ul", "gmina", "gminie", "miejscowości", "w", "terenie", "budowa"}
    return {w for w in re.findall(r"[a-ząćęłńóśźż0-9]{3,}", (s or "").lower()) if w not in stop}


def similarity(a: str, b: str) -> float:
    wa, wb = norm_words(a), norm_words(b)
    jac = len(wa & wb) / len(wa | wb) if wa and wb else 0
    return round(0.5 * jac + 0.5 * SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio(), 3)


SCHEMA = """
CREATE TABLE IF NOT EXISTS projekty(id TEXT PRIMARY KEY, lp INT, nazwa TEXT, beneficjent TEXT, nip TEXT, wojewodztwo TEXT,
  miejscowosc TEXT, id_publiczny TEXT, rodzaj_id TEXT, adnotacja TEXT, pobrano TEXT);
CREATE TABLE IF NOT EXISTS powiazania(projekt TEXT, ocid TEXT, metoda TEXT, pewnosc REAL, status TEXT, podstawa TEXT, pobrano TEXT);
CREATE TABLE IF NOT EXISTS postepowania(ocid TEXT PRIMARY KEY, zamawiajacy TEXT, nip TEXT, przedmiot TEXT, cpv TEXT,
  tryb TEXT, wartosc_szacunkowa REAL, liczba_ofert INT, braki TEXT, strona_postepowania TEXT, uwagi TEXT);
CREATE TABLE IF NOT EXISTS ogloszenia(numer TEXT PRIMARY KEY, typ TEXT, ocid TEXT, data_publikacji TEXT, object_id TEXT, url TEXT, pobrano TEXT);
CREATE TABLE IF NOT EXISTS umowy(ocid TEXT, ogloszenie TEXT, czesc INT, data_zawarcia TEXT, wykonawca TEXT, wartosc REAL,
  wyplacono REAL, zmiana_kosztu_pct REAL, termin_wykonania TEXT, w_terminie TEXT, liczba_zmian INT, nalezycie TEXT,
  termin_planowany TEXT);
CREATE TABLE IF NOT EXISTS zmiany(ocid TEXT, ogloszenie TEXT, podstawa TEXT, przyczyna TEXT);
CREATE TABLE IF NOT EXISTS zdarzenia(projekt TEXT, ocid TEXT, kod TEXT, opis TEXT, ogloszenie TEXT, pole TEXT, cytat TEXT,
  status TEXT DEFAULT 'propozycja – do zatwierdzenia');
CREATE TABLE IF NOT EXISTS log(czas TEXT, operacja TEXT, adres TEXT, wynik TEXT);
CREATE TABLE IF NOT EXISTS decyzje(czas TEXT, akcja TEXT, co TEXT, typ TEXT, projekt TEXT, ocid TEXT, kategoria TEXT, komentarz TEXT, ekspert TEXT);
CREATE TABLE IF NOT EXISTS geo(klucz TEXT PRIMARY KEY, lat REAL, lon REAL, zrodlo TEXT);
CREATE TABLE IF NOT EXISTS korekty(typ TEXT, klucz TEXT, pole TEXT, wartosc TEXT, kto TEXT, czas TEXT, PRIMARY KEY(typ, klucz, pole));
"""


class Db:
    def __init__(self, path: Path):
        self.c = sqlite3.connect(path, check_same_thread=False, timeout=120)
        try:
            self.c.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        self.c.execute("PRAGMA busy_timeout=120000")
        self.c.executescript(SCHEMA)
        for t, col in (("umowy", "termin_planowany"), ("postepowania", "strona_postepowania"), ("postepowania", "uwagi")):
            if col not in [r[1] for r in self.c.execute(f"PRAGMA table_info({t})")]:
                self.c.execute(f"ALTER TABLE {t} ADD COLUMN {col} TEXT")

    def put(self, table: str, row: dict, replace=True):
        cols = ",".join(row)
        q = ",".join("?" * len(row))
        self.c.execute(f"INSERT {'OR REPLACE' if replace else ''} INTO {table}({cols}) VALUES({q})", list(row.values()))

    def clear_project(self, pid: str):
        for t in ("powiazania", "zdarzenia"):
            self.c.execute(f"DELETE FROM {t} WHERE projekt=?", (pid,))


def group_by_ocid(api: Bzp, ocid: str, org_name: str, since: str) -> list[dict]:
    out = []
    for t in TYPES:
        for n in api.all(NoticeType=t, OrganizationName=org_name, PublicationDateFrom=since):
            if n.get("tenderId") == ocid:
                out.append(n)
    uniq = {n["noticeNumber"]: n for n in out}
    return sorted(uniq.values(), key=lambda n: n["publicationDate"])


def link_project(api: Bzp, rec: dict) -> list[dict]:
    nip = digits(rec["Identyfikator odbiorcy"])
    name = rec["Nazwa odbiorcy"]
    pub = rec["Identyfikator publiczny"] or ""
    org_q = re.sub(r"^(Gmina|Miasto i Gmina|Miasto)\s+", "", name).strip()
    num = bzp_base(pub)

    if num:
        cn = api.get(NoticeType="ContractNotice", NoticeNumber=num)
        if cn:
            n = cn[0]
            ok_nip = digits(n.get("organizationNationalId")) == nip
            notes = [f"numer ogłoszenia {num} zgodny", f"NIP {'zgodny' if ok_nip else 'NIEZGODNY'} ({n.get('organizationNationalId')})"]
            grp = group_by_ocid(api, n["tenderId"], n["organizationName"], n["publicationDate"][:19])
            return [{"ocid": n["tenderId"], "metoda": "numer BZP → OCID", "pewnosc": 1.0 if ok_nip else 0.6,
                     "status": "poprawne" if ok_nip else "niepewne", "podstawa": "; ".join(notes), "ogloszenia": grp or cn, "braki": []}]
        found = {}
        for t in TYPES[1:]:
            for n in api.all(NoticeType=t, OrganizationName=org_q):
                if digits(n.get("organizationNationalId")) == nip and num in text(n["htmlBody"]):
                    found.setdefault(n["tenderId"], []).append(n)
        out = []
        for ocid, ns in found.items():
            since = min(x["publicationDate"] for x in ns)[:19]
            grp = group_by_ocid(api, ocid, org_q, "2000-01-01T00:00:00") or ns
            out.append({"ocid": ocid, "metoda": "odwołania w innych ogłoszeniach", "pewnosc": 0.9, "status": "niepewne",
                        "podstawa": f"numer {num} przywołany w {len(ns)} ogłoszeniach ({', '.join(x['noticeNumber'] for x in ns)}); NIP zgodny",
                        "ogloszenia": grp, "braki": [f"Ogłoszenie o zamówieniu {num} – brak w kopii BZP; postępowanie odtworzone z odwołań. Brak w paczce ≠ brak ogłoszenia."]})
        return out or [{"ocid": None, "metoda": "numer BZP", "pewnosc": 0, "status": "brak", "podstawa": f"{num} nie znaleziony ani w ogłoszeniach, ani w odwołaniach", "ogloszenia": [], "braki": []}]

    pool = api.all(NoticeType="ContractNotice", OrganizationName=org_q)
    if rec.get("Siedziba odbiorcy"):
        pool += api.all(NoticeType="ContractNotice", OrganizationCity=rec["Siedziba odbiorcy"])
    cands = list({n["noticeNumber"]: n for n in pool if digits(n.get("organizationNationalId")) == nip}.values())
    title = rec["Nazwa przedsięwzięcia"] or ""
    scored = sorted(((similarity(title, n.get("orderObject", "")), n) for n in cands), key=lambda x: -x[0])[:3]
    out = []
    for k, (sc, n) in enumerate(scored):
        if sc < (0.25 if k == 0 else 0.45):
            continue
        grp = group_by_ocid(api, n["tenderId"], n["organizationName"], n["publicationDate"][:19])
        out.append({"ocid": n["tenderId"], "metoda": "dopasowanie rozmyte (NIP + nazwa)", "pewnosc": sc,
                    "status": "niepewne", "podstawa": f"NIP zgodny; podobieństwo nazwy {round(sc*100)}%: „{n.get('orderObject','')[:90]}”",
                    "ogloszenia": grp, "braki": []})
    return out or [{"ocid": None, "metoda": "dopasowanie rozmyte", "pewnosc": 0, "status": "brak",
                    "podstawa": f"brak ogłoszeń zamawiającego z NIP {nip} o podobnym przedmiocie", "ogloszenia": [], "braki": []}]


PROJ_FIELDS = {"nazwa": "Nazwa przedsięwzięcia", "beneficjent": "Nazwa odbiorcy", "nip": "Identyfikator odbiorcy",
               "wojewodztwo": "Województwo", "miejscowosc": "Siedziba odbiorcy", "id_publiczny": "Identyfikator publiczny",
               "rodzaj_id": "Rodzaj identyfikatora publicznego", "adnotacja": "Adnotacja instytucji"}


def korekty(db: Db, typ: str, klucz: str) -> dict:
    return {p: v for p, v in db.c.execute("SELECT pole, wartosc FROM korekty WHERE typ=? AND klucz=?", (typ, klucz))}


def set_korekta(db: Db, typ: str, klucz: str, pole: str, wartosc, kto: str):
    if wartosc is None or wartosc == "":
        db.c.execute("DELETE FROM korekty WHERE typ=? AND klucz=? AND pole=?", (typ, klucz, pole))
    else:
        db.c.execute("INSERT OR REPLACE INTO korekty VALUES(?,?,?,?,?,?)",
                     (typ, klucz, pole, str(wartosc), kto, datetime.now().isoformat(timespec="seconds")))


def apply_project_fix(db: Db, pid: str, rec: dict) -> dict:
    k = korekty(db, "projekt", pid)
    return {**rec, **{PROJ_FIELDS[f]: v for f, v in k.items() if f in PROJ_FIELDS}}


def rec_from_db(db: Db, pid: str) -> dict | None:
    row = db.c.execute("SELECT lp," + ",".join(PROJ_FIELDS) + " FROM projekty WHERE id=?", (pid,)).fetchone()
    if not row:
        return None
    rec = {"Identyfikator rekordu": pid, "Lp.": row[0]}
    rec.update({PROJ_FIELDS[f]: v for f, v in zip(PROJ_FIELDS, row[1:])})
    return apply_project_fix(db, pid, rec)


def link_manual(api: Bzp, rec: dict, cel: str) -> list[dict]:
    cel = (cel or "").strip()
    if cel.lower().startswith("ocds-"):
        org_q = re.sub(r"^(Gmina|Miasto i Gmina|Miasto)\s+", "", rec["Nazwa odbiorcy"] or "").strip()
        grp = group_by_ocid(api, cel, org_q, "2000-01-01T00:00:00")
        if not grp:
            return []
        ok_nip = digits(grp[0].get("organizationNationalId")) == digits(rec["Identyfikator odbiorcy"])
        return [{"ocid": cel, "metoda": "wskazane przez eksperta (OCID)", "pewnosc": 1.0, "status": "zatwierdzone",
                 "podstawa": f"OCID wskazany przez eksperta; {len(grp)} ogłoszeń; NIP {'zgodny' if ok_nip else 'NIEZGODNY – sprawdź'}",
                 "ogloszenia": grp, "braki": []}]
    if not bzp_base(cel):
        raise ValueError("Podaj numer ogłoszenia BZP (np. 2023/BZP 00129088/01) albo OCID postępowania (ocds-…).")
    links = [l for l in link_project(api, {**rec, "Identyfikator publiczny": cel}) if l["ocid"]]
    for l in links[:1]:
        l.update({"metoda": "wskazane przez eksperta: " + l["metoda"], "status": "zatwierdzone", "pewnosc": 1.0,
                  "podstawa": f"numer {bzp_base(cel)} wskazany przez eksperta; " + l["podstawa"]})
    return links[:1]


def restore_decisions(db: Db, pid: str):
    for akcja, ocid in db.c.execute("SELECT akcja, ocid FROM decyzje WHERE projekt=? AND ocid IS NOT NULL ORDER BY rowid", (pid,)).fetchall():
        new = {"Zatwierdzono": "zatwierdzone", "Poprawiono": "zatwierdzone", "Odrzucono": "odrzucone", "Oznaczono jako wzorcowy": "zatwierdzone"}.get(akcja)
        if new:
            db.c.execute("UPDATE powiazania SET status=? WHERE projekt=? AND ocid=? AND metoda NOT LIKE 'wskazane przez eksperta%'", (new, pid, ocid))


def links_for(db: Db, api: Bzp, pid: str, rec: dict) -> list[dict]:
    cel = korekty(db, "powiazanie", pid).get("cel")
    if cel:
        try:
            got = link_manual(api, rec, cel)
            if got:
                return got
        except ValueError:
            pass
    return link_project(api, rec)


def reprocess(db: Db, api: Bzp, pid: str) -> list[dict]:
    global NOW
    NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = rec_from_db(db, pid)
    if rec is None:
        raise ValueError("Nie ma takiego projektu.")
    links = links_for(db, api, pid, rec)
    db.clear_project(pid)
    for l in links:
        db.put("powiazania", {"projekt": pid, "ocid": l["ocid"], "metoda": l["metoda"], "pewnosc": l["pewnosc"],
               "status": l["status"], "podstawa": l["podstawa"], "pobrano": NOW}, replace=False)
        analyse(pid, l, db)
    restore_decisions(db, pid)
    apply_event_fixes(db, pid)
    db.c.commit()
    return links


EVENT_FIELDS = ("kod", "przyczyna", "skutek_dni", "skutek_zl", "dzialanie", "skutecznosc", "status", "komentarz")
SKUTECZNOSC = ("skuteczne", "częściowo skuteczne", "nieskuteczne", "nie da się ocenić")


def event_key(pid: str, ogloszenie: str, kod: str, pole: str) -> str:
    return f"{pid}|{ogloszenie}|{kod}|{pole}"


def apply_event_fixes(db: Db, pid: str):
    for klucz, pole, w in db.c.execute("SELECT klucz, pole, wartosc FROM korekty WHERE typ='zdarzenie' AND klucz LIKE ? AND pole IN ('kod','status')",
                                       (pid + "|%",)).fetchall():
        _, ogl, kod, fld = klucz.split("|", 3)
        col = "status" if pole == "status" else "kod"
        db.c.execute(f"UPDATE zdarzenia SET {col}=? WHERE projekt=? AND ogloszenie=? AND kod=? AND pole=?", (w, pid, ogl, kod, fld))


def _days(a, b):
    try:
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except Exception:
        return None


def events_detail(db: Db, pid: str) -> list[dict]:
    c = db.c
    rows = c.execute("SELECT z.kod,z.opis,z.ogloszenie,z.pole,z.cytat,z.status,z.ocid,o.url,o.typ FROM zdarzenia z "
                     "LEFT JOIN ogloszenia o ON o.numer=z.ogloszenie WHERE z.projekt=?", (pid,)).fetchall()
    fixes: dict[str, dict] = {}
    for klucz, pole, w, kto, czas in c.execute("SELECT klucz,pole,wartosc,kto,czas FROM korekty WHERE typ='zdarzenie' AND klucz LIKE ?", (pid + "|%",)):
        if pole in ("skutek_dni", "skutek_zl"):
            try:
                w = int(w) if pole == "skutek_dni" else float(w)
            except (TypeError, ValueError):
                pass
        fixes.setdefault(klucz, {})[pole] = {"wartosc": w, "zrodlo": f"ekspert: {kto}, {czas[:16].replace('T', ' ')}"}
    out = []
    for kod, opis, ogl, pole, cyt, status, ocid, url, typ in rows:
        measurable = bool(re.match(r"R(01|06|07|12)", kod or ""))
        u = c.execute("SELECT wartosc,wyplacono,termin_planowany,termin_wykonania,w_terminie FROM umowy WHERE ogloszenie=?", (ogl,)).fetchone() if measurable else None
        scope = f"umowa z ogłoszenia {ogl}"
        if not u and measurable:
            us = c.execute("SELECT wartosc,wyplacono,termin_planowany,termin_wykonania,w_terminie FROM umowy WHERE ocid=?", (ocid,)).fetchall()
            if us:
                u = (sum(x[0] or 0 for x in us) or None, sum(x[1] or 0 for x in us) or None, max((x[2] for x in us if x[2]), default=None),
                     max((x[3] for x in us if x[3]), default=None), "nie" if any((x[4] or "").lower().startswith("nie") for x in us) else (us[0][4] or ""))
                scope = "cały kontrakt, pola 4.4, 5.2 i 5.5 ogłoszeń o wykonaniu"
        auto = {"kod": {"wartosc": kod, "zrodlo": "reguła BidRadar"}, "status": {"wartosc": status, "zrodlo": "system"}}
        if u:
            d = _days(u[2], u[3]) if u[2] and u[3] else None
            if d is not None and d > 0:
                auto["skutek_dni"] = {"wartosc": d, "zrodlo": f"BZP: termin planowany {u[2]} → wykonanie {u[3]} ({scope})"}
            if u[0] and u[1]:
                zl = lambda v: f"{v:,.2f}".replace(",", " ").replace(".", ",")
                auto["skutek_zl"] = {"wartosc": round(u[1] - u[0], 2), "zrodlo": f"BZP: wypłacono {zl(u[1])} − wartość umowy {zl(u[0])} zł ({scope})"}
        ch = c.execute("SELECT ogloszenie,podstawa,przyczyna FROM zmiany WHERE ocid=? ORDER BY ogloszenie", (ocid,)).fetchall() \
            if re.match(r"R(01|06|07|12)", kod or "") else []
        if typ == "AgreementUpdateNotice":
            auto["przyczyna"] = {"wartosc": re.sub(r"\s+", " ", cyt or "")[:400], "zrodlo": f"BZP: pole 5.3 ogłoszenia {ogl}"}
            auto["dzialanie"] = {"wartosc": "zmiana umowy (aneks): " + re.sub(r"\s+", " ", opis.split("(", 1)[-1].rstrip(")"))[:160],
                                 "zrodlo": f"BZP: pole 5.2 ogłoszenia {ogl}"}
        elif ch:
            n0, pod0, prz0 = ch[0]
            auto["przyczyna"] = {"wartosc": re.sub(r"\s+", " ", prz0 or "")[:400], "zrodlo": f"BZP: pole 5.3 ogłoszenia o zmianie umowy {n0}"}
            auto["dzialanie"] = {"wartosc": f"zmiana umowy ({len(ch)}×): " + re.sub(r"\s+", " ", pod0 or "")[:140], "zrodlo": f"BZP: pole 5.2 ogłoszenia {n0}"}
        if "dzialanie" in auto and u and u[4]:
            ok = not str(u[4]).lower().startswith("nie")
            auto["skutecznosc"] = {"wartosc": "skuteczne" if ok else "nieskuteczne",
                                   "zrodlo": "BZP pole 5.3: umowa po zmianie wykonana " + ("w terminie" if ok else "po terminie") + " – ocena automatyczna, do potwierdzenia"}
        key = event_key(pid, ogl, kod if "|" not in kod else kod, pole)
        f = {**auto, **fixes.get(event_key(pid, ogl, kod, pole), {})}
        orig = next((k for k in fixes if k.startswith(f"{pid}|{ogl}|") and k.endswith(f"|{pole}") and fixes[k].get("kod", {}).get("wartosc") == kod), None)
        if orig:
            f = {**auto, **fixes[orig]}
            key = orig
        out.append({"klucz": key, "kod": f["kod"]["wartosc"], "opis": opis, "ogloszenie": ogl, "pole": pole, "cytat": cyt, "url": url,
                    "status": f["status"]["wartosc"], "poprawione": bool(fixes.get(key)),
                    **{k: f.get(k) for k in ("przyczyna", "skutek_dni", "skutek_zl", "dzialanie", "skutecznosc", "komentarz")}})
    return out

def analyse(pid: str, link: dict, db: Db):
    ocid, ns = link["ocid"], link["ogloszenia"]
    if not ocid:
        return
    by = {t: [n for n in ns if n["noticeType"] == t] for t in TYPES}
    cn = by["ContractNotice"][0] if by["ContractNotice"] else None
    res = by["TenderResultNotice"][0] if by["TenderResultNotice"] else None
    any_n = cn or res or ns[0]
    rt = text(res["htmlBody"]) if res else ""
    offers = field(r"Liczba otrzymanych ofert[^:]*:\s*(\d+)", rt)
    db.put("postepowania", {"ocid": ocid, "zamawiajacy": any_n.get("organizationName"), "nip": any_n.get("organizationNationalId"),
           "przedmiot": any_n.get("orderObject"), "cpv": (any_n.get("cpvCode") or "")[:10], "tryb": any_n.get("tenderType"),
           "wartosc_szacunkowa": money(field(r"Wartość zamówienia[^:]*:\s*([\d\s,\.]+)\s*PLN", text(cn["htmlBody"]) if cn else "")),
           "liczba_ofert": int(offers) if offers else None, "braki": json.dumps(link["braki"], ensure_ascii=False),
           "strona_postepowania": field(r"Adres strony internetowej prowadzonego postępowania:\s*(\S+)", text(cn["htmlBody"]) if cn else "")})
    for n in ns:
        db.put("ogloszenia", {"numer": n["noticeNumber"], "typ": n["noticeType"], "ocid": ocid, "data_publikacji": n["publicationDate"],
               "object_id": n["objectId"], "url": NOTICE_URL.format(n["objectId"]), "pobrano": NOW})

    def ev(kod, opis, n, pole, cyt):
        db.put("zdarzenia", {"projekt": pid, "ocid": ocid, "kod": kod, "opis": opis, "ogloszenie": n["noticeNumber"], "pole": pole, "cytat": cyt}, replace=False)

    if offers and int(offers) <= 2:
        ev("R11/R12", f"Mała konkurencja: {offers} ofert(y)", res, "Liczba otrzymanych ofert", quote(rt, "Liczba otrzymanych ofert"))
    db.c.execute("DELETE FROM umowy WHERE ocid=?", (ocid,))
    db.c.execute("DELETE FROM zmiany WHERE ocid=?", (ocid,))
    seen, perf, dups = {}, [], []
    for p in by["ContractPerformingNotice"]:
        s = text(p["htmlBody"])
        key = (field(r"4\.1\.\) Data zawarcia umowy[^:]*:\s*(\S+)", s), field(r"4\.4\.\) Wartość umowy[^:]*:\s*([\d\s,\.]+)", s).strip(),
               field(r"5\.5\.\)[^:]*:\s*([\d\s,\.]+)", s).strip(), field(r"4\.3\.1\.\)[^:]*\):\s*(.*?)\n", s)[:60].lower())
        if key in seen:
            dups.append((seen[key]["noticeNumber"], p["noticeNumber"]))
            seen[key] = p
        else:
            seen[key] = p
    perf = sorted(seen.values(), key=lambda n: n["publicationDate"])
    if dups:
        notes = [f"Zduplikowane ogłoszenie o wykonaniu tej samej umowy: {a} i {b} – policzone jako jedna umowa." for a, b in dups]
        db.c.execute("UPDATE postepowania SET uwagi=? WHERE ocid=?", (json.dumps(notes, ensure_ascii=False), ocid))
    for i, p in enumerate(perf, 1):
        s = text(p["htmlBody"])
        wu = money(field(r"4\.4\.\) Wartość umowy[^:]*:\s*([\d\s,\.]+)", s))
        wy = money(field(r"5\.5\.\)[^:]*:\s*([\d\s,\.]+)", s))
        pct = round((wy - wu) / wu * 100, 1) if wu and wy else None
        row = {"ocid": ocid, "ogloszenie": p["noticeNumber"], "czesc": i,
               "data_zawarcia": field(r"4\.1\.\) Data zawarcia umowy[^:]*:\s*(\S+)", s),
               "wykonawca": field(r"4\.3\.1\.\)[^:]*\):\s*(.*?)\n", s)[:120], "wartosc": wu, "wyplacono": wy, "zmiana_kosztu_pct": pct,
               "termin_wykonania": field(r"5\.2\.\)[^:]*:\s*(\S+)", s), "w_terminie": field(r"5\.3\.\)[^:]*:\s*(\S+)", s),
               "liczba_zmian": int(field(r"5\.4\.1\.\)[^:]*:\s*(\d+)", s) or 0), "nalezycie": field(r"5\.6\.\)[^:]*:\s*(\S+)", s),
               "termin_planowany": plan_date(s, field(r"4\.1\.\) Data zawarcia umowy[^:]*:\s*(\S+)", s))}
        db.put("umowy", row, replace=False)
        if row["w_terminie"].lower().startswith("nie"):
            ev("R06", "Umowa wykonana po terminie", p, "5.3", quote(s, "5.3.)"))
        if pct is not None and pct > 15:
            ev("R01/R07", f"Koszt końcowy {pct:+.1f}% względem wartości umowy", p, "4.4 / 5.5", quote(s, "5.5.)"))
        if row["nalezycie"].lower().startswith("nie"):
            ev("R12", "Wykonanie oznaczone jako nienależyte" + (f"; wypłacono {pct:+.1f}%" if pct is not None else ""), p, "5.6", quote(s, "5.6.)"))
    for u in by["AgreementUpdateNotice"]:
        s = text(u["htmlBody"])
        pod = field(r"5\.2\.\) Podstawa prawna[^:]*:\s*(.*?)5\.3", s)
        prz = field(r"5\.3\.\) Przyczyny[^:]*:\s*(.*?)5\.4", s)
        db.put("zmiany", {"ocid": ocid, "ogloszenie": u["noticeNumber"], "podstawa": pod[:300], "przyczyna": prz[:600]}, replace=False)
        kod = "R07" if "pkt 4" in pod else "R01" if "pkt 3" in pod else "R07"
        ev(kod, f"Zmiana umowy ({re.sub(r'\s+', ' ', pod)[:80]})", u, "5.2 / 5.3", re.sub(r"\s+", " ", prz)[:200])


def export(db: Db, out: Path):
    out.mkdir(exist_ok=True)
    c = db.c
    c.row_factory = sqlite3.Row
    projs = []
    for p in c.execute("SELECT * FROM projekty ORDER BY lp"):
        p = dict(p)
        p["powiazania"] = [dict(r) for r in c.execute("SELECT * FROM powiazania WHERE projekt=? ORDER BY pewnosc DESC", (p["id"],))]
        for l in p["powiazania"]:
            if l["ocid"]:
                l["postepowanie"] = dict(c.execute("SELECT * FROM postepowania WHERE ocid=?", (l["ocid"],)).fetchone() or {})
                l["ogloszenia"] = [dict(r) for r in c.execute("SELECT * FROM ogloszenia WHERE ocid=? ORDER BY data_publikacji", (l["ocid"],))]
                l["umowy"] = [dict(r) for r in c.execute("SELECT * FROM umowy WHERE ocid=?", (l["ocid"],))]
                l["zmiany"] = [dict(r) for r in c.execute("SELECT * FROM zmiany WHERE ocid=?", (l["ocid"],))]
        p["zdarzenia"] = [dict(r) for r in c.execute("SELECT * FROM zdarzenia WHERE projekt=?", (p["id"],))]
        projs.append(p)
    (out / "projekty.json").write_text(json.dumps(projs, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out / "raport_polaczen.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["lp", "projekt", "identyfikator", "status", "metoda", "pewnosc", "ocid", "liczba_ogloszen", "podstawa"])
        for p in projs:
            for l in p["powiazania"] or [{}]:
                w.writerow([p["lp"], p["nazwa"], p["id_publiczny"], l.get("status"), l.get("metoda"), l.get("pewnosc"), l.get("ocid"),
                            len(l.get("ogloszenia", [])), l.get("podstawa")])
    with open(out / "zdarzenia.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["projekt", "kod", "opis", "ogloszenie", "pole", "cytat", "url", "status"])
        for r in c.execute("SELECT z.*, o.url FROM zdarzenia z LEFT JOIN ogloszenia o ON o.numer=z.ogloszenie"):
            w.writerow([r["projekt"], r["kod"], r["opis"], r["ogloszenie"], r["pole"], r["cytat"], r["url"], r["status"]])
    c.row_factory = None
    return projs


def main():
    ap = argparse.ArgumentParser(description="BidRadar – import i wzbogacenie z BZP")
    ap.add_argument("xlsx", type=Path)
    ap.add_argument("--url", default=os.environ.get("RM_BZP_URL", "http://127.0.0.1:8010"))
    ap.add_argument("--db", type=Path, default=Path(os.environ.get("RM_DB", "bidradar.db")))
    ap.add_argument("--out", type=Path, default=Path("out"))
    a = ap.parse_args()
    try:
        run(a.xlsx, a.url, a.db, a.out)
    except RuntimeError as e:
        sys.exit(str(e))


def run(xlsx: Path, url: str, dbpath: Path, outdir: Path, say=print) -> dict:
    global NOW
    NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    class A: pass
    a = A(); a.xlsx, a.url, a.db, a.out = Path(xlsx), url, Path(dbpath), Path(outdir)
    print = say
    db = Db(a.db)
    log = lambda op, adr, wyn: db.put("log", {"czas": datetime.now().isoformat(timespec="seconds"), "operacja": op, "adres": adr, "wynik": str(wyn)}, replace=False)
    api = Bzp(a.url, log)
    if not a.xlsx.exists():
        raise RuntimeError(f"Nie ma pliku {a.xlsx}. Podaj ścieżkę do Risk_Miner_dane_projekty.xlsx.")
    if not api.check():
        raise RuntimeError(f"Brak odpowiedzi z API BZP pod {api.base}\n  {api.last_error}\n"
                 "Sprawdź, czy serwer kopii działa (python3 bzp_mirror.py) i na jakim porcie – adres podasz przez --url.")
    n_log0 = db.c.execute("SELECT COUNT(*) FROM log WHERE operacja='GET'").fetchone()[0]
    recs = read_xlsx(a.xlsx)
    print(f"Arkusz: {len(recs)} rekordów · API: {api.base}\n")
    t0 = time.time()
    for r in recs:
        pid = r["Identyfikator rekordu"]
        r = apply_project_fix(db, pid, r)
        db.put("projekty", {"id": pid, "lp": int(float(r["Lp."])), "nazwa": r["Nazwa przedsięwzięcia"], "beneficjent": r["Nazwa odbiorcy"],
               "nip": digits(r["Identyfikator odbiorcy"]), "wojewodztwo": r["Województwo"], "miejscowosc": r["Siedziba odbiorcy"],
               "id_publiczny": r["Identyfikator publiczny"], "rodzaj_id": r["Rodzaj identyfikatora publicznego"],
               "adnotacja": r.get("Adnotacja instytucji"), "pobrano": NOW})
        links = links_for(db, api, pid, r)
        db.clear_project(pid)
        for l in links:
            db.put("powiazania", {"projekt": pid, "ocid": l["ocid"], "metoda": l["metoda"], "pewnosc": l["pewnosc"],
                   "status": l["status"], "podstawa": l["podstawa"], "pobrano": NOW}, replace=False)
            analyse(pid, l, db)
        restore_decisions(db, pid)
        apply_event_fixes(db, pid)
        best = links[0]
        kinds = {}
        for n in best["ogloszenia"]:
            kinds[n["noticeType"]] = kinds.get(n["noticeType"], 0) + 1
        print(f"{r['Lp.']:>3} {r['Nazwa odbiorcy'][:24]:<24} {str(r['Identyfikator publiczny'])[:26]:<26} "
              f"{best['status']:<9} {best['pewnosc']:<5} ogł.: {'/'.join(str(kinds.get(t, 0)) for t in TYPES)}  ({best['metoda']})")
        db.c.commit()
    projs = export(db, a.out)
    st = {}
    for p in projs:
        s = (p["powiazania"][0]["status"] if p["powiazania"] else "brak")
        st[s] = st.get(s, 0) + 1
    ev = db.c.execute("SELECT COUNT(*) FROM zdarzenia").fetchone()[0]
    print(f"\nGotowe w {time.time()-t0:.0f} s · powiązania: {st} · zdarzenia ryzyka: {ev}")
    print("Kolumny ogł.: zamówienie/wynik/zmiana/wykonanie")
    print(f"Baza: {a.db} · eksport: {a.out}/projekty.json, raport_polaczen.csv, zdarzenia.csv")
    return {"projekty": len(projs), "powiazania": st, "zdarzenia": ev, "sekundy": round(time.time() - t0, 1),
            "zapytania": db.c.execute("SELECT COUNT(*) FROM log WHERE operacja='GET'").fetchone()[0] - n_log0, "czas": NOW}


if __name__ == "__main__":
    main()
