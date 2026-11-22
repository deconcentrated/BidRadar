#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import rm_config as cfg
import rm_ingest as ing

SCHEMA = """
CREATE TABLE IF NOT EXISTS ted(numer TEXT PRIMARY KEY, tytul TEXT, zamawiajacy TEXT, id_zamawiajacego TEXT, data TEXT,
  typ TEXT, cpv TEXT, wartosc REAL, wykonawca TEXT, url TEXT, pobrano TEXT, projekt TEXT, pewnosc REAL, metoda TEXT, podstawa TEXT, status TEXT);
"""
FIELDS = ["publication-number", "notice-title", "buyer-name", "buyer-identifier", "publication-date", "notice-type",
          "classification-cpv", "total-value", "winner-name"]
FIELDS_MIN = ["publication-number", "notice-title", "buyer-name", "publication-date"]
DETAIL_URL = "https://ted.europa.eu/pl/notice/-/detail/{}"


def pick(v, lang=("pol", "POL", "eng", "ENG")):
    if v is None:
        return None
    if isinstance(v, dict):
        for k in lang:
            if k in v:
                return pick(v[k])
        return pick(next(iter(v.values()), None))
    if isinstance(v, list):
        vals = [pick(x) for x in v if x not in (None, "")]
        vals = [x for x in vals if x]
        return "; ".join(dict.fromkeys(str(x) for x in vals)) if vals else None
    return str(v)


def to_num(v) -> float | None:
    s = pick(v)
    if not s:
        return None
    m = re.search(r"\d[\d\s.,]*", s)
    if not m:
        return None
    t = m.group(0).replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(",", "") if t.rfind(".") > t.rfind(",") else t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def notice_url(n: dict, num: str) -> str:
    links = n.get("links") or {}
    for kind in ("html", "htmlDirect", "pdf"):
        u = pick((links.get(kind) or {}), lang=("POL", "pol", "ENG", "eng")) if isinstance(links.get(kind), dict) else None
        if u:
            return u.split("; ")[0]
    return DETAIL_URL.format(num)


class Ted:
    def __init__(self, conf: dict, log):
        self.url = os.environ.get("RM_TED_URL") or conf.get("url") or "https://api.ted.europa.eu/v3/notices/search"
        self.conf = conf
        self.log = log
        self.last_error = ""
        self.query = self.build_query()

    def build_query(self) -> str:
        cpv = " ".join(self.conf.get("cpv") or ["45232000"])
        since = (self.conf.get("od") or "2021-01-01").replace("-", "")
        return f"classification-cpv IN ({cpv}) AND buyer-country={self.conf.get('kraj', 'POL')} AND publication-date>={since}"

    def post(self, body: dict) -> dict:
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
                                              "User-Agent": "BidRadar/1.0 (HackNation 2026, MFiPR Risk Miner)"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    def search(self, limit: int = 2000) -> list[dict]:
        out, token, fields = [], None, FIELDS
        while len(out) < limit:
            body = {"query": self.query, "fields": fields, "limit": min(250, limit - len(out)), "scope": "ALL",
                    "paginationMode": "ITERATION", "onlyLatestVersions": True}
            if token:
                body["iterationNextToken"] = token
            try:
                data = self.post(body)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                self.log("BŁĄD TED", self.url, f"HTTP {e.code}: {detail}")
                if e.code == 400 and fields is FIELDS:
                    fields = FIELDS_MIN
                    continue
                self.last_error = f"HTTP {e.code}: {detail}"
                break
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                self.log("BŁĄD TED", self.url, self.last_error)
                break
            page = data.get("notices") or []
            self.log("TED", self.url + " · " + self.query, len(page))
            out += page
            token = data.get("iterationNextToken")
            if not page or not token:
                break
            time.sleep(0.3)
        return out


def link(db: ing.Db, rec: dict) -> tuple[str | None, float | None, str | None, str | None]:
    buyer_id = re.sub(r"\D", "", rec["id_zamawiajacego"] or "")
    best = (None, None, None, None)
    for pid, benef, nip, city in db.c.execute("SELECT id, beneficjent, nip, miejscowosc FROM projekty"):
        nip = re.sub(r"\D", "", nip or "")
        if nip and len(nip) == 10 and nip in buyer_id:
            return pid, .95, "NIP zamawiającego (TED)", f"identyfikator zamawiającego w TED zawiera NIP {nip}"
        sim = ing.similarity(rec["zamawiajacy"] or "", benef or "")
        same_place = bool(city) and city.lower() in ((rec["zamawiajacy"] or "") + " " + (rec["tytul"] or "")).lower()
        if sim >= .85 and same_place and (best[1] or 0) < .7:
            best = (pid, .7, "nazwa zamawiającego (TED)", f"nazwa podobna w {round(sim * 100)}%, ta sama miejscowość – do decyzji eksperta")
    return best


def run(db: ing.Db, say=print) -> dict:
    db.c.executescript(SCHEMA)
    conf = cfg.get(db.c, "zrodla").get("ted") or {}
    if not conf.get("wlaczone", True):
        return {"ok": False, "stan": "wyłączone w konfiguracja/zrodla.json", "ogloszenia": 0, "powiazane": 0}
    def log(op, adr, wyn):
        db.put("log", {"czas": datetime.now().isoformat(timespec="seconds"), "operacja": op,
                       "adres": adr[:500], "wynik": str(wyn)[:500]}, replace=False)
        db.c.commit()
    t0 = time.time()
    api = Ted(conf, log)
    notices = api.search(int(conf.get("limit_ogloszen") or 2000))
    now = datetime.now().isoformat(timespec="seconds")
    n_link = 0
    for n in notices:
        num = pick(n.get("publication-number"))
        if not num:
            continue
        rec = {"numer": num, "tytul": pick(n.get("notice-title")), "zamawiajacy": pick(n.get("buyer-name")),
               "id_zamawiajacego": pick(n.get("buyer-identifier")), "data": (pick(n.get("publication-date")) or "")[:10],
               "typ": pick(n.get("notice-type")), "cpv": pick(n.get("classification-cpv")), "wartosc": to_num(n.get("total-value")),
               "wykonawca": pick(n.get("winner-name")), "url": notice_url(n, num), "pobrano": now}
        pid, pew, met, pod = link(db, rec)
        n_link += bool(pid)
        prev = db.c.execute("SELECT status, projekt FROM ted WHERE numer=?", (num,)).fetchone()
        status = prev[0] if prev and prev[1] == pid and prev[0] in ("zatwierdzone", "odrzucone") else \
            ("poprawne" if pew and pew >= .9 else "niepewne" if pid else None)
        db.put("ted", {**rec, "projekt": pid, "pewnosc": pew, "metoda": met, "podstawa": pod, "status": status})
    db.c.commit()
    ok = bool(notices) or not api.last_error
    res = {"ok": ok, "stan": "OK" if ok else f"brak odpowiedzi TED: {api.last_error}", "ogloszenia": len(notices),
           "powiazane": n_link, "zapytanie": api.query, "sekundy": round(time.time() - t0, 1), "czas": now}
    say(f"TED: {len(notices)} ogłoszeń, powiązanych z projektami: {n_link} ({res['stan']})")
    return res


def stats(db: ing.Db) -> dict:
    db.c.executescript(SCHEMA)
    c = db.c
    last = c.execute("SELECT czas, operacja, wynik FROM log WHERE operacja IN ('TED','BŁĄD TED') ORDER BY rowid DESC LIMIT 1").fetchone()
    return {"ogloszenia": c.execute("SELECT COUNT(*) FROM ted").fetchone()[0],
            "powiazane": c.execute("SELECT COUNT(DISTINCT projekt) FROM ted WHERE projekt IS NOT NULL").fetchone()[0],
            "zapytania": c.execute("SELECT COUNT(*) FROM log WHERE operacja='TED'").fetchone()[0],
            "ostatnie": last[0] if last else None, "blad": last[2] if last and last[1] == "BŁĄD TED" else None}


def for_project(db: ing.Db, pid: str) -> list[dict]:
    db.c.executescript(SCHEMA)
    return [dict(zip(("numer", "typ", "data", "url", "tytul", "pewnosc", "metoda"), r)) for r in db.c.execute(
        "SELECT numer, typ, data, url, tytul, pewnosc, metoda FROM ted WHERE projekt=? AND status IN ('poprawne','zatwierdzone') "
        "ORDER BY data", (pid,))]


if __name__ == "__main__":
    db = ing.Db(Path(os.environ.get("RM_DB", "bidradar.db")))
    r = run(db)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    sys.exit(0 if r["ok"] else 1)
