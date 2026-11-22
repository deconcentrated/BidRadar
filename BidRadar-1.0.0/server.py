#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import socket
import sqlite3
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(HERE / ".env")

import rm_auth as auth
import rm_benchmark as bench
import rm_config as cfg
import rm_docs as docs
import rm_ingest as ing
import rm_invest as invest
import rm_llm as llm
import rm_record as record
import rm_ted as ted

VOIV = {"dolnośląskie": (51.05, 16.4), "kujawsko-pomorskie": (53.1, 18.5), "lubelskie": (51.2, 22.85), "lubuskie": (52.2, 15.3),
        "łódzkie": (51.6, 19.4), "małopolskie": (49.85, 20.2), "mazowieckie": (52.35, 21.1), "opolskie": (50.65, 17.85),
        "podkarpackie": (49.95, 22.15), "podlaskie": (53.3, 22.95), "pomorskie": (54.2, 18.0), "śląskie": (50.35, 19.0),
        "świętokrzyskie": (50.8, 20.75), "warmińsko-mazurskie": (53.85, 20.8), "wielkopolskie": (52.4, 17.1),
        "zachodniopomorskie": (53.55, 15.4)}
LOCK = threading.RLock()
REFRESH_LOCK = threading.Lock()
GEO_ONLINE = [True]
AUTH_ON = os.environ.get("RM_AUTH", "on").lower() not in ("off", "0", "nie", "false")
MAX_BODY = int(float(os.environ.get("RM_MAX_MB", 40)) * 1024 * 1024)
DOC_EXT = (".docx", ".doc", ".pdf", ".txt", ".md", ".odt", ".rtf", ".html", ".htm")
LOCAL_USER = {"login": "lokalny", "nazwa": "użytkownik lokalny", "rola": "zatwierdzajacy", "rola_opis": "tryb bez logowania"}

ALERT_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerty(id INTEGER PRIMARY KEY AUTOINCREMENT, czas TEXT, projekt TEXT, ocid TEXT, ogloszenie TEXT,
  zrodlo TEXT, typ TEXT, opis TEXT, url TEXT, istotne INT, status TEXT DEFAULT 'nowy');
"""

subsector = bench.subsector


def geocode(db: ing.Db, city: str, woj: str) -> tuple[float, float, str]:
    key = f"{city}|{woj}"
    row = db.c.execute("SELECT lat, lon, zrodlo FROM geo WHERE klucz=?", (key,)).fetchone()
    if row:
        return row
    if not GEO_ONLINE[0] or os.environ.get("RM_OFFLINE"):
        return fallback(key, woj)
    q = urllib.parse.urlencode({"q": f"{city}, {woj}, Polska", "format": "json", "limit": 1, "countrycodes": "pl"})
    try:
        req = urllib.request.Request("https://nominatim.openstreetmap.org/search?" + q,
                                     headers={"User-Agent": "BidRadar/1.0 (HackNation 2026, prototyp MFiPR)"})
        with urllib.request.urlopen(req, timeout=6) as r:
            res = json.load(r)
        time.sleep(1.1)
        if res:
            lat, lon, src = float(res[0]["lat"]), float(res[0]["lon"]), "Nominatim (OpenStreetMap)"
            db.c.execute("INSERT OR REPLACE INTO geo VALUES(?,?,?,?)", (key, lat, lon, src))
            db.c.commit()
            return lat, lon, src
    except Exception:
        GEO_ONLINE[0] = False
    return fallback(key, woj)


def fallback(key: str, woj: str):
    lat, lon = VOIV.get((woj or "").lower(), (52.05, 19.3))
    rnd = random.Random(key)
    return lat + rnd.uniform(-.35, .35), lon + rnd.uniform(-.5, .5), "środek województwa (brak geokodowania)"


def risk_code(kod: str) -> str:
    return (kod or "").split("/")[0]


def projects(db: ing.Db) -> list[dict]:
    c = db.c
    out = []
    for lp, pid, nazwa, benef, woj, miasto, idp, rodzaj in c.execute(
            "SELECT lp,id,nazwa,beneficjent,wojewodztwo,miejscowosc,id_publiczny,rodzaj_id FROM projekty ORDER BY lp").fetchall():
        links = c.execute("SELECT ocid,metoda,pewnosc,status,podstawa FROM powiazania WHERE projekt=? ORDER BY pewnosc DESC", (pid,)).fetchall()
        best = next((l for l in links if l[3] in ("zatwierdzone", "poprawne")), links[0] if links else None)
        lat, lon, geo_src = geocode(db, miasto or benef, woj)
        kpo = "kpo" in (rodzaj or "").lower()
        fx = ing.korekty(db, "projekt", pid)
        rec = {f: v for f, v in zip(ing.PROJ_FIELDS, c.execute("SELECT " + ",".join(ing.PROJ_FIELDS) + " FROM projekty WHERE id=?", (pid,)).fetchone())}
        p = {"id": lp, "rid": pid, "n": benef, "t": nazwa, "idf": ing.bzp_base(idp) or idp, "sub": subsector(nazwa),
             "rec": rec, "fixed": sorted(fx), "manual_link": ing.korekty(db, "powiazanie", pid).get("cel"),
             "ll": [round(lat, 4), round(lon, 4)], "geo": geo_src, "woj": woj, "kpo": kpo}
        if best and best[0]:
            ocid = best[0]
            p.update({"ocid": ocid, "link": {"metoda": best[1], "pewnosc": best[2], "status": best[3], "podstawa": best[4]}})
            if kpo and best[3] == "zatwierdzone":
                p["kpo"] = False
            u = c.execute("SELECT data_zawarcia,wartosc,wyplacono,termin_wykonania,w_terminie,liczba_zmian,nalezycie,termin_planowany "
                          "FROM umowy WHERE ocid=? ORDER BY czesc", (ocid,)).fetchall()
            post = c.execute("SELECT liczba_ofert,braki,strona_postepowania FROM postepowania WHERE ocid=?", (ocid,)).fetchone()
            if u and not p["kpo"]:
                v = sum(x[1] or 0 for x in u)
                paid = sum(x[2] or 0 for x in u)
                p.update({"v": round(v, 2) or None, "paid": round(paid, 2) or None,
                          "late": any((x[4] or "").lower().startswith("nie") for x in u),
                          "bad": any((x[6] or "").lower().startswith("nie") for x in u) or None,
                          "ch": sum(x[5] or 0 for x in u), "sign": min(x[0] for x in u if x[0]) if any(x[0] for x in u) else None,
                          "end": max((x[3] for x in u if x[3]), default=None), "plan": max((x[7] for x in u if x[7]), default=None) or None})
                if len(u) > 1:
                    p["parts"] = len(u)
            if post:
                p["off"] = post[0]
                p["platform"] = post[2] or None
                if json.loads(post[1] or "[]"):
                    p["gap"] = True
                    p["braki"] = json.loads(post[1])
            ch = c.execute("SELECT podstawa, przyczyna FROM zmiany WHERE ocid=? LIMIT 1", (ocid,)).fetchone()
            if ch and not p["kpo"]:
                p["reason"] = re.sub(r"\s+", " ", f"{ch[0]} – {ch[1]}")[:220]
            ev = c.execute("SELECT kod FROM zdarzenia WHERE projekt=? AND status NOT LIKE 'odrzuc%' ORDER BY "
                           "CASE WHEN kod LIKE 'R12%' THEN 0 WHEN kod LIKE 'R01%' THEN 1 WHEN kod LIKE 'R07%' THEN 2 ELSE 3 END", (pid,)).fetchone()
            if ev and not p["kpo"] and (p.get("reason") or p.get("bad")):
                p["risk"] = risk_code(ev[0])
            p["events"] = ing.events_detail(db, pid)
            p["notices"] = [dict(zip(("numer", "typ", "data", "url"), r)) for r in
                            c.execute("SELECT numer,typ,data_publikacji,url FROM ogloszenia WHERE ocid=? ORDER BY data_publikacji", (ocid,))]
        tn = ted.for_project(db, pid)
        if tn:
            p["ted"] = tn
            p.setdefault("notices", []).extend({"numer": t["numer"], "typ": f"TED: {t['typ'] or 'ogłoszenie'}", "data": t["data"] or "",
                                                "url": t["url"]} for t in tn)
            p["notices"].sort(key=lambda n: n.get("data") or "")
        open_alerts = c.execute("SELECT COUNT(*) FROM alerty WHERE projekt=? AND status='nowy'", (pid,)).fetchone()[0]
        if open_alerts:
            p["alerts"] = open_alerts
        out.append({k: v for k, v in p.items() if v is not None})
    return out


def doc_listing(db: ing.Db) -> list[dict]:
    out = docs.listing(db)
    dec = {(r[0], r[1]): r[2] for r in db.c.execute("SELECT projekt, kategoria, akcja FROM decyzje WHERE typ IN ('dokument','weryfikacja') ORDER BY rowid")}
    st = {"Zatwierdzono": "zatwierdzono", "Poprawiono": "poprawiono", "Odrzucono": "odrzucono",
          "Zatwierdzono fragment": "zatwierdzono", "Odrzucono fragment": "odrzucono", "Poprawiono fragment": "poprawiono"}
    for d in out:
        for f in d["fragmenty"]:
            a = dec.get((d["id"], f"{f['kod']}|{f['miejsce']}|{f['cytat'][:40]}"))
            if a:
                f["status"] = st.get(a, f["status"])
        for v in d["weryfikacja"]:
            a = dec.get((d["id"], v["pole"]))
            if a:
                v["decyzja"] = st.get(a, a)
    return out


def queue(db: ing.Db) -> list[dict]:
    q = []
    for aid, pid, ogl, zr, typ, opis, url, ist, benef in db.c.execute(
            "SELECT a.id,a.projekt,a.ogloszenie,a.zrodlo,a.typ,a.opis,a.url,a.istotne,p.beneficjent FROM alerty a "
            "LEFT JOIN projekty p ON p.id=a.projekt WHERE a.status='nowy' ORDER BY a.istotne DESC, a.id DESC"):
        q.append({"c": ["l" if ist else "m", "istotna zmiana" if ist else "nowe ogłoszenie"], "layer": "src", "typ": "alert", "alert": aid,
                  "projekt": pid, "title": f"Monitoring: {typ}",
                  "body": f"{benef or 'projekt spoza arkusza'} · {zr} <a href=\"{url}\" target=\"_blank\" rel=\"noopener\">{ogl}</a>",
                  "why": opis + " Zatwierdź, gdy zapoznasz się ze zmianą; odrzuć, jeśli nie dotyczy projektu."})
    for d in doc_listing(db):
        benef = db.c.execute("SELECT beneficjent FROM projekty WHERE id=?", (d["projekt"],)).fetchone() if d["projekt"] else None
        for v in d["weryfikacja"]:
            if v["wynik"] not in ("niezgodne", "brak w BZP") or v.get("decyzja"):
                continue
            q.append({"c": ["m", "reguła"], "layer": "calc", "typ": "weryfikacja", "dokument": d["id"], "pole": v["pole"],
                      "title": f"Niezgodność dokumentu z BZP: {v['pole']}",
                      "body": f"{benef[0] if benef else ''} · {d['nazwa']}: dokument „{v['dokument']}” ↔ BZP „{v['bzp']}”",
                      "why": v["komentarz"] or "Dane z dokumentu i z ogłoszenia BZP się różnią – wskaż, które są prawidłowe."})
    for pid, lp, benef, idp, ocid, met, pew, pod in db.c.execute(
            "SELECT w.projekt,p.lp,p.beneficjent,p.id_publiczny,w.ocid,w.metoda,w.pewnosc,w.podstawa FROM powiazania w "
            "JOIN projekty p ON p.id=w.projekt WHERE w.status='niepewne' ORDER BY w.pewnosc DESC, p.lp"):
        first = db.c.execute("SELECT MAX(pewnosc) FROM powiazania WHERE projekt=?", (pid,)).fetchone()[0]
        if pew < first:
            continue
        lvl = "h" if pew >= .9 else "m" if pew >= .6 else "l"
        gap = "odwołania" in (met or "")
        q.append({"c": [lvl, f"{round(pew * 100)}%"], "layer": "calc", "projekt": pid, "ocid": ocid,
                  "title": "Luka w danych: brak ogłoszenia o zamówieniu" if gap else "Dopasowanie projektu bez numeru BZP",
                  "body": f"{benef} · <i>{idp}</i> ↔ postępowanie <i>{(ocid or '')[-12:]}</i>",
                  "why": f"{met}: {pod}"})
    for num, tyt, url, pid, pew, pod, benef in db.c.execute(
            "SELECT t.numer,t.tytul,t.url,t.projekt,t.pewnosc,t.podstawa,p.beneficjent FROM ted t JOIN projekty p ON p.id=t.projekt "
            "WHERE t.status='niepewne' ORDER BY t.pewnosc"):
        q.append({"c": ["m", f"{round((pew or 0) * 100)}%"], "layer": "calc", "typ": "ted", "numer": num, "projekt": pid,
                  "title": "Powiązanie z ogłoszeniem TED",
                  "body": f"{benef} ↔ TED <a href=\"{url}\" target=\"_blank\" rel=\"noopener\">{num}</a> · {(tyt or '')[:120]}",
                  "why": f"nazwa zamawiającego (TED): {pod}"})
    return q


class App:
    def __init__(self, a):
        self.a = a
        self._local = threading.local()
        self.docdir = HERE / "dokumenty"
        self.testdir = HERE / "test_wejscie"
        self.last = None
        self.busy = False
        self.ted_state = {"stan": "jeszcze nie pobierano"}
        self.monitor = {"wlaczony": False, "co_ile_godzin": 0, "ostatnio": None, "nastepne": None, "nowe": 0, "stan": "—"}
        c = self.db.c
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(bench.SCHEMA)
        c.executescript(docs.SCHEMA)
        c.executescript(ted.SCHEMA)
        c.executescript(ALERT_SCHEMA)
        c.execute(cfg.SCHEMA)
        auth.setup(c)
        c.commit()
        self.bench_state = "gotowa" if not bench.stale(self.db) else ("do przebudowy" if self.bench_count() else "brak")

    @property
    def db(self) -> ing.Db:
        d = getattr(self._local, "db", None)
        if d is None:
            d = ing.Db(self.a.db)
            self._local.db = d
        return d

    def bench_count(self) -> int:
        try:
            return self.db.c.execute("SELECT COUNT(*) FROM umowy_sektor").fetchone()[0]
        except Exception:
            return 0

    def process_docs(self, only: Path | None = None, hint: str | None = None) -> int:
        self.docdir.mkdir(exist_ok=True)
        files = [only] if only else sorted(f for f in self.docdir.iterdir() if f.suffix.lower() in DOC_EXT)
        n = 0
        for f in files:
            try:
                r = docs.process(self.db, f, say=lambda *x: None)
                if hint:
                    row = self.db.c.execute("SELECT projekt FROM dokumenty WHERE id=?", (r["id"],)).fetchone()
                    if row and not row[0]:
                        pid = self.db.c.execute("SELECT id FROM projekty WHERE lp=?", (int(hint),)).fetchone()
                        if pid:
                            self.db.c.execute("UPDATE dokumenty SET projekt=?, metoda='przypisanie użytkownika', pewnosc=1, podstawa='wskazane przy wgraniu' WHERE id=?", (pid[0], r["id"]))
                            self.db.c.commit()
                n += 1
            except Exception as e:
                print(f"  dokument {f.name}: {e}")
        return n

    def build_bench(self):
        if self.bench_state == "w toku":
            return
        self.bench_state = "w toku"
        api = ing.Bzp(self.a.url, lambda *x: None)
        if not api.check():
            self.bench_state = "błąd: brak API BZP"
            return
        n = bench.build(self.db, api, say=lambda *x: None)
        self.bench_state = "gotowa" if n else "błąd"
        print(f"Grupa porównawcza gotowa: {n} umów.")

    def refresh(self, with_ted: bool = True) -> dict:
        if not REFRESH_LOCK.acquire(blocking=False):
            return {"ok": False, "w_toku": True, "blad": "Import już trwa – wyniki pojawią się za chwilę."}
        self.busy = True
        try:
            res = ing.run(self.a.xlsx, self.a.url, self.a.db, self.a.out, say=lambda *x, **k: None)
            self.last = res
            res["dokumenty"] = self.process_docs()
            if with_ted:
                threading.Thread(target=self.refresh_ted, daemon=True).start()
                res["ted"] = "pobieranie w tle"
            return {"ok": True, **res}
        except Exception as e:
            return {"ok": False, "blad": str(e)}
        finally:
            self.busy = False
            REFRESH_LOCK.release()

    def refresh_ted(self) -> dict:
        self.ted_state = {"stan": "w toku"}
        try:
            r = ted.run(self.db, say=lambda *x: None)
        except Exception as e:
            r = {"ok": False, "stan": f"błąd: {e}"}
        self.ted_state = r
        return r

    def run_monitor(self, simulate: bool = False) -> dict:
        c = self.db.c
        sim_note = ""
        if simulate:
            row = c.execute("SELECT numer, ocid FROM ogloszenia WHERE typ='AgreementUpdateNotice' ORDER BY data_publikacji DESC LIMIT 1").fetchone()
            if row:
                c.execute("DELETE FROM ogloszenia WHERE numer=?", (row[0],))
                c.commit()
                sim_note = " (symulacja: ogłoszenie usunięte z bazy przed sprawdzeniem)"
        before = {r[0] for r in c.execute("SELECT numer FROM ogloszenia")}
        before_ted = {r[0] for r in c.execute("SELECT numer FROM ted")}
        r = self.refresh(with_ted=False)
        if not r.get("ok"):
            self.monitor.update({"ostatnio": now(), "stan": "błąd: " + r.get("blad", "")})
            return {"ok": False, "blad": r.get("blad"), "nowe": 0}
        if not simulate and (cfg.get(c, "zrodla").get("ted") or {}).get("wlaczone", True):
            self.refresh_ted()
        new = 0
        open_projects = {r[0] for r in c.execute(
            "SELECT DISTINCT w.projekt FROM powiazania w WHERE w.status IN ('poprawne','zatwierdzone','niepewne') AND NOT EXISTS "
            "(SELECT 1 FROM ogloszenia o WHERE o.ocid=w.ocid AND o.typ='ContractPerformingNotice')")}
        for num, typ, ocid, url, data in c.execute("SELECT numer, typ, ocid, url, data_publikacji FROM ogloszenia").fetchall():
            if num in before:
                continue
            pid = c.execute("SELECT projekt FROM powiazania WHERE ocid=? ORDER BY pewnosc DESC LIMIT 1", (ocid,)).fetchone()
            pid = pid[0] if pid else None
            ch = c.execute("SELECT podstawa, przyczyna FROM zmiany WHERE ogloszenie=?", (num,)).fetchone()
            basis = re.sub(r"\s+", " ", f"{ch[0] or ''} {ch[1] or ''}").strip() if ch else ""
            important = typ == "AgreementUpdateNotice" and bool(re.search(r"455\s*ust\.?\s*1\s*pkt\s*4|istotn|wynagrodzeni|termin", basis, re.I))
            label = {"AgreementUpdateNotice": "nowe ogłoszenie o zmianie umowy", "ContractPerformingNotice": "ogłoszenie o wykonaniu umowy",
                     "TenderResultNotice": "ogłoszenie o wyniku postępowania", "ContractNotice": "nowe ogłoszenie o zamówieniu"}.get(typ, typ)
            opis = (f"Opublikowano {data[:10] if data else ''}: {label}." + (f" Podstawa: {basis[:200]}." if basis else "")
                    + (" Projekt jest w toku." if pid in open_projects else "") + sim_note)
            c.execute("INSERT INTO alerty(czas,projekt,ocid,ogloszenie,zrodlo,typ,opis,url,istotne) VALUES(?,?,?,?,?,?,?,?,?)",
                      (now(), pid, ocid, num, "BZP", label, opis, url, int(important)))
            new += 1
        for num, pid, url, typ, data in c.execute("SELECT numer, projekt, url, typ, data FROM ted WHERE projekt IS NOT NULL").fetchall():
            if num not in before_ted and before_ted:
                c.execute("INSERT INTO alerty(czas,projekt,ocid,ogloszenie,zrodlo,typ,opis,url,istotne) VALUES(?,?,?,?,?,?,?,?,0)",
                          (now(), pid, None, num, "TED", "nowe ogłoszenie w TED", f"Opublikowano {data}: {typ or 'ogłoszenie'}.", url))
                new += 1
        c.commit()
        self.monitor.update({"ostatnio": now(), "nowe": new, "projekty_w_toku": len(open_projects),
                             "stan": f"OK · {new} nowych ogłoszeń" + (" (symulacja)" if simulate else "")})
        return {"ok": True, "nowe": new, "projekty_w_toku": len(open_projects), "symulacja": simulate}

    def monitor_loop(self, hours: float):
        self.monitor.update({"wlaczony": True, "co_ile_godzin": hours})
        while True:
            nxt = time.time() + hours * 3600
            self.monitor["nastepne"] = datetime.fromtimestamp(nxt).isoformat(timespec="minutes")
            time.sleep(hours * 3600)
            try:
                self.run_monitor()
            except Exception as e:
                self.monitor["stan"] = f"błąd: {e}"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def links_report(db: ing.Db) -> list[dict]:
    c, out = db.c, []
    for lp, pid, idp, benef, ocid, met, pew, st, pod in c.execute(
            "SELECT p.lp,p.id,p.id_publiczny,p.beneficjent,w.ocid,w.metoda,w.pewnosc,w.status,w.podstawa FROM powiazania w "
            "JOIN projekty p ON p.id=w.projekt ORDER BY p.lp, w.pewnosc DESC"):
        n = c.execute("SELECT COUNT(*) FROM ogloszenia WHERE ocid=?", (ocid,)).fetchone()[0]
        d = c.execute("SELECT czas, akcja, ekspert, komentarz FROM decyzje WHERE projekt=? AND ocid=? ORDER BY rowid DESC LIMIT 1", (pid, ocid)).fetchone()
        out.append({"lp": lp, "projekt": pid, "beneficjent": benef, "identyfikator": idp, "zrodlo": "BZP", "powiazanie": ocid,
                    "status": st, "metoda": met, "pewnosc": pew, "liczba_ogloszen": n, "podstawa": pod,
                    "decyzja": d[1] if d else None, "ekspert": d[2] if d else None, "czas_decyzji": d[0] if d else None, "komentarz": d[3] if d else None})
    for lp, pid, idp, benef, num, met, pew, st, pod in c.execute(
            "SELECT p.lp,p.id,p.id_publiczny,p.beneficjent,t.numer,t.metoda,t.pewnosc,t.status,t.podstawa FROM ted t "
            "JOIN projekty p ON p.id=t.projekt ORDER BY p.lp"):
        out.append({"lp": lp, "projekt": pid, "beneficjent": benef, "identyfikator": idp, "zrodlo": "TED", "powiazanie": num,
                    "status": st, "metoda": met, "pewnosc": pew, "liczba_ogloszen": 1, "podstawa": pod,
                    "decyzja": None, "ekspert": None, "czas_decyzji": None, "komentarz": None})
    return out


def export_db(db: ing.Db) -> dict:
    skip = {"uzytkownicy", "sesje", "logowania", "sqlite_sequence"}
    out = {"_opis": "BidRadar – pełna baza (SQLite → JSON). Tabele: projekty, powiazania, postepowania, ogloszenia, umowy, zmiany, "
                    "zdarzenia, decyzje, dokumenty, fragmenty, weryfikacja, roznice, ted, alerty, umowy_sektor, ustawienia, log …",
           "_wygenerowano": now()}
    for (t,) in db.c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        if t in skip:
            continue
        cur = db.c.execute(f'SELECT * FROM "{t}"')
        cols = [d[0] for d in cur.description]
        out[t] = [dict(zip(cols, r)) for r in cur.fetchall()]
    return out


PAGES = {"/": "landing.html", "/index.html": "landing.html", "/app": "bidradar_ui.html", "/app/": "bidradar_ui.html",
         "/login": "login.html"}
NEED = {"/api/docs/upload": "analityk", "/api/test/record": "analityk",
        "/api/decision": "zatwierdzajacy", "/api/refresh": "zatwierdzajacy", "/api/settings": "zatwierdzajacy",
        "/api/monitor/run": "zatwierdzajacy", "/api/ted/refresh": "zatwierdzajacy",
        "/api/decision/batch": "zatwierdzajacy", "/api/ai/test": "analityk",
        "/api/event": "zatwierdzajacy", "/api/project": "zatwierdzajacy", "/api/link/change": "zatwierdzajacy"}

ROUTES = [
    ("GET", "/healthz", "Stan serwera, grupy porównawczej i importu", None, "System"),
    ("GET", "/api/me", "Zalogowany użytkownik i rola", None, "Konta"),
    ("POST", "/api/login", "Logowanie: {login, haslo} → ciasteczko sesji HttpOnly", None, "Konta"),
    ("POST", "/api/logout", "Wylogowanie", None, "Konta"),
    ("GET", "/api/projects", "Projekty z powiązaniami BZP/TED, umowami, zmianami i zdarzeniami R01–R15", None, "Dane"),
    ("GET", "/api/sources", "Źródła danych, liczby rekordów, dziennik zapytań, stan modelu językowego", None, "Dane"),
    ("GET", "/api/ted", "Ogłoszenia TED powiązane z projektami", None, "Dane"),
    ("GET", "/api/docs", "Dokumenty, fragmenty z cytatem, weryfikacja z BZP, różnice", None, "Dokumenty"),
    ("POST", "/api/docs/upload", "Wgranie dokumentu (DOCX/DOC/PDF/OCR/ODT/RTF/HTML/TXT) do projektu", "analityk", "Dokumenty"),
    ("GET", "/api/queue", "Kolejka eksperta: od najmniej pewnych, alerty na górze", None, "Ekspert"),
    ("GET", "/api/decisions", "Historia decyzji: kto, kiedy, co, komentarz", None, "Ekspert"),
    ("POST", "/api/decision", "Decyzja: Zatwierdzono / Poprawiono / Odrzucono / Oznaczono jako wzorcowy", "zatwierdzajacy", "Ekspert"),
    ("POST", "/api/decision/batch", "Zbiorcze zatwierdzenie połączeń z kolejki o pewności ≥ min_pewnosc (domyślnie 0,9); każda decyzja zapisana osobno", "zatwierdzajacy", "Ekspert"),
    ("POST", "/api/event", "Poprawka zdarzenia: kategoria, przyczyna, skutek (dni, zł), działanie zaradcze, skuteczność, status", "zatwierdzajacy", "Ekspert"),
    ("POST", "/api/project", "Poprawka rekordu projektu (nazwa, NIP, numer BZP…); przetrwa ponowny import", "zatwierdzajacy", "Ekspert"),
    ("POST", "/api/link/change", "Zmiana powiązania: numer ogłoszenia BZP albo OCID wskazany przez eksperta (pusty = powrót do automatu)", "zatwierdzajacy", "Ekspert"),
    ("GET", "/api/settings", "Katalog ryzyk, wagi podobieństwa, podsektory, konfiguracja", None, "Ekspert"),
    ("POST", "/api/settings", "Zmiana katalogu ryzyk, wag lub reguł podsektorów (z autorem)", "zatwierdzajacy", "Ekspert"),
    ("POST", "/api/analyze", "Analiza nowej inwestycji z punktu/obszaru/opisu (JSON)", None, "Analiza"),
    ("POST", "/api/test/invest", "Analiza nowej inwestycji z pliku (multipart) → md/json/html/docx", None, "Analiza"),
    ("POST", "/api/test/invest-text", "Analiza nowej inwestycji z tekstu formularza", None, "Analiza"),
    ("POST", "/api/test/record", "Import rekordu projektu (xlsx/csv/json/docx/txt) i uzupełnienie z BZP", "analityk", "Analiza"),
    ("GET", "/api/analyses", "Historia analiz nowych inwestycji (?id= pełny wynik)", None, "Analiza"),
    ("POST", "/api/explain", "Wyjaśnienie wyniku modelem językowym (opcjonalne)", None, "Analiza"),
    ("POST", "/api/ai/test", "Test połączenia z modelem językowym: czas odpowiedzi, dostawca, maskowanie danych", "analityk", "Analiza"),
    ("GET", "/api/monitor", "Monitoring projektów w toku: alerty", None, "Monitoring"),
    ("POST", "/api/monitor/run", "Sprawdzenie nowych ogłoszeń teraz", "zatwierdzajacy", "Monitoring"),
    ("POST", "/api/refresh", "Ponowny import z BZP", "zatwierdzajacy", "Monitoring"),
    ("POST", "/api/ted/refresh", "Ponowne pobranie z TED", "zatwierdzajacy", "Monitoring"),
    ("GET", "/api/export/record", "Eksport rekordu ?id=&fmt=md|json|csv|html|docx", None, "Eksport"),
    ("GET", "/api/export/records", "Eksport wielu rekordów ?ids=&fmt=md|json|csv|html|docx", None, "Eksport"),
    ("GET", "/api/export/links", "Raport połączeń (CSV)", None, "Eksport"),
    ("GET", "/api/export/db", "Pełna baza jako paczka JSON", None, "Eksport"),
    ("GET", "/api/subsector", "Podsektor dla nazwy inwestycji wg aktualnych reguł (?nazwa=)", None, "Ekspert"),
    ("GET", "/api/openapi.json", "Ten opis API (OpenAPI 3.0)", None, "System"),
]


def openapi() -> dict:
    paths: dict = {}
    for m, pth, summ, role, tag in ROUTES:
        op = {"summary": summ, "tags": [tag], "responses": {"200": {"description": "OK"}}}
        if role:
            op["security"] = [{"sesja": []}]
            op["description"] = f"Wymaga zalogowania; minimalna rola: {role}."
            op["responses"].update({"401": {"description": "Brak logowania"}, "403": {"description": "Za niska rola"}})
        paths.setdefault(pth, {})[m.lower()] = op
    return {"openapi": "3.0.3", "info": {"title": "BidRadar API", "version": "1.0",
            "description": "Mapa ryzyk inwestycji wod-kan (MFiPR Risk Miner). Odczyt i analiza bez logowania; zmiany po zalogowaniu (role)."},
            "components": {"securitySchemes": {"sesja": {"type": "apiKey", "in": "cookie", "name": auth.COOKIE}}}, "paths": paths}


def api_page() -> bytes:
    rows = "".join(f"<tr><td><b>{m}</b></td><td><code>{p}</code></td><td>{t}</td><td>{r or 'bez logowania'}</td><td>{s}</td></tr>"
                   for m, p, s, r, t in ROUTES)
    return ("<!doctype html><html lang=pl><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>BidRadar API</title>"
            "<style>body{font:15px/1.5 system-ui,sans-serif;margin:24px;color:#1f2937;max-width:1100px}h1{font-size:22px;margin:0 0 4px}"
            "table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid #e5e7eb;vertical-align:top}"
            "th{background:#f0fdf4}code{font:13px ui-monospace,monospace}a{color:#15803d}.w{overflow-x:auto}</style>"
            "<h1>BidRadar API</h1><p>Specyfikacja maszynowa: <a href='/api/openapi.json'>/api/openapi.json</a> (OpenAPI 3.0, do Swagger UI, Postmana i generatorów klientów). "
            "Odpowiedzi w JSON, błędy jako <code>{ok:false, blad}</code>. Zmiany wymagają ciasteczka sesji z <code>/api/login</code>.</p>"
            "<div class=w><table><tr><th>Metoda</th><th>Adres</th><th>Grupa</th><th>Rola</th><th>Opis</th></tr>" + rows + "</table></div>").encode()


class HttpError(Exception):
    def __init__(self, code: int, msg: str, **extra):
        super().__init__(msg)
        self.code, self.msg, self.extra = code, msg, extra


def handler(app: App):
    class H(BaseHTTPRequestHandler):
        server_version = "BidRadar/1.0"
        sys_version = ""

        def log_message(self, fmt, *a):
            if os.environ.get("RM_ACCESS_LOG"):
                sys.stderr.write(f"{self.address_string()} {fmt % a}\n")

        def headers_common(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

        def send(self, code, body, ctype="application/json; charset=utf-8", extra: dict | None = None):
            b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.headers_common()
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(b)

        def send_file(self, f: Path, ctype: str, download: bool = False):
            b = f.read_bytes()
            extra = {"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(f.name)} if download else None
            self.send(200, b, ctype, extra)

        def redirect(self, to: str, cookie: str | None = None):
            self.send_response(303)
            self.send_header("Location", to)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.headers_common()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def token(self) -> str | None:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == auth.COOKIE:
                    return v
            return None

        def user(self) -> dict | None:
            if not AUTH_ON:
                return LOCAL_USER
            return auth.session_user(app.db.c, self.token())

        def ip(self) -> str:
            return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

        def cookie(self, value: str, max_age: int) -> str:
            secure = os.environ.get("RM_SECURE_COOKIE") == "1" or self.headers.get("X-Forwarded-Proto") == "https"
            return f"{auth.COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}" + ("; Secure" if secure else "")

        def same_origin(self) -> bool:
            src = self.headers.get("Origin") or self.headers.get("Referer")
            if not src:
                return True
            host = urllib.parse.urlparse(src).netloc
            return host in {self.headers.get("Host"), self.headers.get("X-Forwarded-Host")}

        def body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise HttpError(413, f"Plik jest za duży. Limit to {MAX_BODY // 1024 // 1024} MB – podziel dokument albo zwiększ RM_MAX_MB.")
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {}
            ctype = self.headers.get("Content-Type") or ""
            if "application/x-www-form-urlencoded" in ctype:
                return {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", "replace")).items()}
            try:
                d = json.loads(raw)
            except ValueError:
                raise HttpError(400, "Nieprawidłowe dane żądania (oczekiwany JSON).")
            if not isinstance(d, dict):
                raise HttpError(400, "Nieprawidłowe dane żądania (oczekiwany obiekt JSON).")
            return d

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            path, q = u.path, urllib.parse.parse_qs(u.query)
            try:
                if path in PAGES:
                    f = HERE / PAGES[path]
                    if path == "/login" and self.user() and AUTH_ON:
                        return self.redirect(safe_next((q.get("next") or ["/app"])[0]))
                    if not f.exists():
                        return self.send(404, f"Brak pliku {f.name} obok server.py".encode(), "text/plain; charset=utf-8")
                    return self.send_file(f, "text/html; charset=utf-8")
                if path.startswith("/static/") or path == "/favicon.ico":
                    name = "favicon.png" if path == "/favicon.ico" else urllib.parse.unquote(path[8:])
                    f = (HERE / "static" / name).resolve()
                    ct = {".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}.get(f.suffix)
                    if ct and (HERE / "static").resolve() in f.parents and f.is_file():
                        return self.send(200, f.read_bytes(), ct, {"Cache-Control": "public, max-age=86400"})
                    return self.send(404, {"blad": "Nie znaleziono."})
                if path == "/healthz":
                    app.db.c.execute("SELECT 1").fetchone()
                    return self.send(200, {"ok": True, "czas": now(), "grupa_porownawcza": app.bench_state, "import_w_toku": app.busy})
                if path == "/api/subsector":
                    n = (q.get("nazwa") or [""])[0][:400]
                    return self.send(200, {"nazwa": n, "podsektor": subsector(n)})
                if path == "/api/openapi.json":
                    return self.send(200, openapi())
                if path in ("/api", "/api/"):
                    return self.send(200, api_page(), "text/html; charset=utf-8")
                if path == "/api/analyses":
                    c = app.db.c
                    aid = (q.get("id") or [""])[0]
                    if aid:
                        r = c.execute("SELECT rowid,czas,opis,wynik FROM analizy WHERE rowid=?", (aid,)).fetchone()
                        if not r:
                            return self.send(404, {"blad": "Nie ma takiej analizy."})
                        return self.send(200, {"id": r[0], "czas": r[1], "opis": r[2], "wynik": json.loads(r[3] or "{}")})
                    lim = min(int((q.get("limit") or ["50"])[0] or 50), 500)
                    out = []
                    for rid, czas, opis, wynik in c.execute("SELECT rowid,czas,opis,wynik FROM analizy ORDER BY rowid DESC LIMIT ?", (lim,)):
                        w = json.loads(wynik or "{}")
                        st = w.get("statystyka") or {}
                        out.append({"id": rid, "czas": czas, "opis": (opis or "")[:160], "n_umow": st.get("n_umow"),
                                    "zakres": st.get("zakres"), "po_terminie_pct": (st.get("termin") or {}).get("udzial_po_terminie"),
                                    "koszt_p75_pct": (st.get("koszt") or {}).get("p75")})
                    return self.send(200, {"liczba": c.execute("SELECT COUNT(*) FROM analizy").fetchone()[0], "analizy": out})
                if path == "/api/me":
                    u_ = self.user()
                    return self.send(200, {"auth": AUTH_ON, "zalogowany": bool(u_), **({k: u_[k] for k in ("login", "nazwa", "rola", "rola_opis") if k in u_} if u_ else {})})
                if path == "/api/projects":
                    return self.send(200, projects(app.db))
                if path == "/api/docs":
                    return self.send(200, doc_listing(app.db))
                if path == "/api/queue":
                    return self.send(200, queue(app.db))
                if path == "/api/decisions":
                    rows = app.db.c.execute("SELECT czas,akcja,co,typ,projekt,kategoria,komentarz,ekspert FROM decyzje ORDER BY rowid DESC").fetchall()
                    return self.send(200, [dict(zip(("czas", "akcja", "co", "typ", "projekt", "kategoria", "komentarz", "ekspert"), r)) for r in rows])
                if path == "/api/sources":
                    c = app.db.c
                    last = c.execute("SELECT MAX(pobrano) FROM projekty").fetchone()[0]
                    st = dict(c.execute("SELECT status, COUNT(DISTINCT projekt) FROM powiazania GROUP BY status").fetchall())
                    return self.send(200, {"bzp_url": app.a.url, "projekty": c.execute("SELECT COUNT(*) FROM projekty").fetchone()[0],
                                           "ogloszenia": c.execute("SELECT COUNT(*) FROM ogloszenia").fetchone()[0],
                                           "zapytania": c.execute("SELECT COUNT(*) FROM log WHERE operacja='GET'").fetchone()[0],
                                           "bledy": c.execute("SELECT COUNT(*) FROM log WHERE operacja='BŁĄD'").fetchone()[0],
                                           "ostatni_import": last, "statusy": st, "zdarzenia": c.execute("SELECT COUNT(*) FROM zdarzenia").fetchone()[0],
                                           "luki": c.execute("SELECT COUNT(*) FROM powiazania WHERE metoda LIKE 'odwołania%'").fetchone()[0],
                                           "w_toku": app.busy, "grupa_porownawcza": app.bench_count(), "grupa_stan": app.bench_state,
                                           "ai": llm.status(), "ted": {**ted.stats(app.db), "stan": app.ted_state.get("stan")},
                                           "monitoring": {**app.monitor, "alerty_nowe": c.execute("SELECT COUNT(*) FROM alerty WHERE status='nowy'").fetchone()[0]},
                                           "auth": AUTH_ON})
                if path == "/api/settings":
                    c = app.db.c
                    return self.send(200, {"wagi": cfg.get(c, "wagi"), "wagi_zmiana": cfg.meta(c, "wagi"),
                                           "katalog": cfg.get(c, "katalog") if cfg.meta(c, "katalog") else None, "katalog_zmiana": cfg.meta(c, "katalog"),
                                           "podsektory": cfg.file("podsektory"), "zrodla": cfg.get(c, "zrodla")})
                if path == "/api/monitor":
                    rows = app.db.c.execute("SELECT id,czas,projekt,ogloszenie,zrodlo,typ,opis,url,istotne,status FROM alerty ORDER BY id DESC LIMIT 200").fetchall()
                    return self.send(200, {**app.monitor, "alerty": [dict(zip(("id", "czas", "projekt", "ogloszenie", "zrodlo", "typ", "opis", "url", "istotne", "status"), r)) for r in rows]})
                if path == "/api/ted":
                    rows = app.db.c.execute("SELECT numer,tytul,zamawiajacy,data,typ,wartosc,wykonawca,url,projekt,pewnosc,metoda,status FROM ted ORDER BY data DESC LIMIT 500").fetchall()
                    return self.send(200, {**ted.stats(app.db), "stan": app.ted_state, "ogloszenia_lista": [dict(zip(
                        ("numer", "tytul", "zamawiajacy", "data", "typ", "wartosc", "wykonawca", "url", "projekt", "pewnosc", "metoda", "status"), r)) for r in rows]})
                if path == "/api/export/db":
                    stamp = datetime.now().strftime("%Y%m%d_%H%M")
                    b = json.dumps(export_db(app.db), ensure_ascii=False, default=str).encode()
                    return self.send(200, b, "application/json; charset=utf-8",
                                     {"Content-Disposition": f"attachment; filename=bidradar_baza_{stamp}.json"})
                if path == "/api/export/links":
                    rows = links_report(app.db)
                    if (q.get("fmt") or ["csv"])[0] == "json":
                        return self.send(200, rows)
                    import csv as csvmod
                    import io
                    buf = io.StringIO()
                    cols = list(rows[0]) if rows else ["lp", "projekt", "status"]
                    w = csvmod.DictWriter(buf, fieldnames=cols, delimiter=";")
                    w.writeheader()
                    w.writerows(rows)
                    return self.send(200, ("\ufeff" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8",
                                     {"Content-Disposition": f"attachment; filename=raport_polaczen_{datetime.now():%Y%m%d_%H%M}.csv"})
                if path in ("/api/export/record", "/api/export/records"):
                    fmt = (q.get("fmt") or ["md"])[0]
                    fmt = fmt if fmt in ("md", "json", "csv", "html", "docx") else "md"
                    outdir = Path(app.a.out) / "eksport"
                    with LOCK:
                        if path == "/api/export/record":
                            rid = (q.get("id") or [""])[0]
                            if not app.db.c.execute("SELECT 1 FROM projekty WHERE id=?", (rid,)).fetchone():
                                return self.send(404, {"blad": "Nie ma takiego projektu."})
                            f = Path(record.save(record.export_one(app.db, rid, [], "baza BidRadar"), outdir)[fmt])
                        else:
                            ids = [i for i in ",".join(q.get("ids") or []).split(",") if i] or \
                                  [r[0] for r in app.db.c.execute("SELECT id FROM projekty ORDER BY lp")]
                            f = record.export_many(app.db, ids, outdir, fmt)
                    ct = {"csv": "text/csv; charset=utf-8", "md": "text/markdown; charset=utf-8", "html": "text/html; charset=utf-8",
                          "docx": DOCX_CT}.get(fmt, "application/json; charset=utf-8")
                    return self.send_file(f, ct, download=fmt != "html")
                if path.startswith("/out/"):
                    root = Path(app.a.out).resolve()
                    f = (root / urllib.parse.unquote(path[5:])).resolve()
                    if root in f.parents and f.is_file():
                        ct = {".csv": "text/csv; charset=utf-8", ".md": "text/markdown; charset=utf-8", ".json": "application/json; charset=utf-8",
                              ".html": "text/html; charset=utf-8", ".docx": DOCX_CT}.get(f.suffix, "application/octet-stream")
                        return self.send_file(f, ct, download="dl=1" in (u.query or ""))
                return self.send(404, {"blad": "Nie znaleziono."})
            except HttpError as e:
                return self.send(e.code, {"ok": False, "blad": e.msg, **e.extra})
            except Exception:
                return self.fail()

        def fail(self):
            try:
                app.db.c.rollback()
            except Exception:
                pass
            ref = uuid.uuid4().hex[:8]
            sys.stderr.write(f"[błąd {ref}] {self.command} {self.path}\n{traceback.format_exc()}\n")
            return self.send(500, {"ok": False, "blad": f"Wewnętrzny błąd serwera (kod {ref}). Szczegóły są w konsoli serwera.", "kod": ref})

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            try:
                if not self.same_origin():
                    raise HttpError(403, "Żądanie z innej strony zostało zablokowane.")
                body = self.body()
                if path == "/api/login":
                    return self.login(body)
                if path == "/api/logout":
                    auth.end_session(app.db.c, self.token())
                    if body.get("form"):
                        return self.redirect("/", self.cookie("", 0))
                    return self.send(200, {"ok": True}, extra={"Set-Cookie": self.cookie("", 0)})
                user = self.user()
                need = NEED.get(path) or (NEED["/api/test/record"] if path.startswith("/api/test/record") else None)
                if need and not auth.allowed(user, need):
                    if not user:
                        raise HttpError(401, "Zaloguj się, aby zapisać zmiany. Przeglądanie danych nie wymaga logowania.", login="/login?next=/app")
                    raise HttpError(403, f"Twoje konto ({user.get('rola_opis') or user['rola']}) nie ma uprawnień do tej operacji. "
                                         "Poproś eksperta zatwierdzającego.")
                try:
                    return self.route_post(path, body, user)
                except BaseException:
                    try:
                        app.db.c.rollback()
                    except Exception:
                        pass
                    raise
            except HttpError as e:
                return self.send(e.code, {"ok": False, "blad": e.msg, **e.extra})
            except Exception:
                return self.fail()

        def login(self, body: dict):
            login, pw, ip = body.get("login", ""), body.get("haslo", ""), self.ip()
            form = bool(body.get("form"))
            nxt = safe_next(body.get("next") or "/app")
            if not AUTH_ON:
                return self.redirect(nxt) if form else self.send(200, {"ok": True, **LOCAL_USER})
            with LOCK:
                if auth.too_many_failures(app.db.c, login, ip):
                    msg = "Zbyt wiele nieudanych prób. Odczekaj 10 minut i spróbuj ponownie."
                    return self.redirect(f"/login?blad=limit&next={urllib.parse.quote(nxt)}") if form else self.send(429, {"ok": False, "blad": msg})
                u_ = auth.check_password(app.db.c, login, pw, ip)
                if not u_:
                    return self.redirect(f"/login?blad=haslo&login={urllib.parse.quote(login[:60])}&next={urllib.parse.quote(nxt)}") if form else \
                        self.send(401, {"ok": False, "blad": "Login lub hasło się nie zgadzają. Sprawdź je i spróbuj jeszcze raz."})
                tok = auth.new_session(app.db.c, u_["login"], ip)
            ck = self.cookie(tok, int(auth.SESSION_H * 3600))
            return self.redirect(nxt, ck) if form else self.send(200, {"ok": True, **u_}, extra={"Set-Cookie": ck})

        def route_post(self, path: str, body: dict, user: dict | None):
            who = (user or {}).get("nazwa") or (user or {}).get("login") or "anonim"
            if path == "/api/refresh":
                r = app.refresh()
                return self.send(409 if r.get("w_toku") else 200, r)
            if path == "/api/ted/refresh":
                threading.Thread(target=app.refresh_ted, daemon=True).start()
                return self.send(202, {"ok": True, "stan": "pobieranie w tle"})
            if path == "/api/monitor/run":
                return self.send(200, app.run_monitor(simulate=bool(body.get("symulacja"))))
            if path == "/api/settings":
                key, val = body.get("klucz"), body.get("wartosc")
                if key == "wagi":
                    try:
                        val = cfg.validate_weights(val or {})
                    except ValueError as e:
                        raise HttpError(400, str(e))
                elif key == "katalog":
                    if not isinstance(val, list) or not all(isinstance(x, dict) and re.fullmatch(r"R\d{2,3}", str(x.get("c", ""))) for x in val):
                        raise HttpError(400, "Katalog ryzyk: oczekiwana lista pozycji z kodem w formacie R01.")
                    val = [{"c": x["c"], "n": str(x.get("n", ""))[:200], "e": str(x.get("e", ""))[:300], "on": bool(x.get("on", True))} for x in val]
                elif key == "podsektory":
                    val = save_subsectors(val)
                else:
                    raise HttpError(400, "Nieznane ustawienie. Dostępne: wagi, katalog, podsektory.")
                with LOCK:
                    cfg.put(app.db.c, key, val, who)
                    if not body.get("cicho"):
                        app.db.c.execute("INSERT INTO decyzje VALUES(?,?,?,?,?,?,?,?,?)",
                                         (now(), "Zmieniono ustawienie", "wagi podobieństwa" if key == "wagi" else key,
                                          "katalog" if key == "katalog" else "ustawienia", None, None, key,
                                          json.dumps(val, ensure_ascii=False)[:500], who))
                    app.db.c.commit()
                if key == "podsektory":
                    bench.BENCH_V = "2-" + cfg.rules_hash()
                    threading.Thread(target=app.build_bench, daemon=True).start()
                return self.send(200, {"ok": True, "wartosc": val})
            if path == "/api/docs/upload":
                name = re.sub(r"[^\w.\-ąćęłńóśźżĄĆĘŁŃÓŚŹŻ ]", "_", Path(body.get("name") or "dokument").name)[:120]
                if Path(name).suffix.lower() not in DOC_EXT:
                    return self.send(200, {"ok": False, "blad": "Obsługiwane formaty: DOCX, DOC, PDF (także skany), ODT, RTF, HTML, TXT."})
                try:
                    data = base64.b64decode(body.get("b64") or "", validate=False)
                except ValueError:
                    raise HttpError(400, "Nie udało się odczytać przesłanego pliku.")
                app.docdir.mkdir(exist_ok=True)
                f = unique_path(app.docdir / name, data)
                f.write_bytes(data)
                with LOCK:
                    n = app.process_docs(only=f, hint=body.get("projekt"))
                    lst = doc_listing(app.db)
                d = next((x for x in lst if x["nazwa"] == f.name), None)
                return self.send(200, {"ok": bool(n and d), "dokument": d, "blad": None if d else "Nie udało się odczytać pliku."})
            if path == "/api/test/invest-text":
                t = (body.get("opis") or "").strip()
                if not t:
                    raise HttpError(400, "Wpisz opis inwestycji albo wgraj plik z opisem.")
                extra = []
                if body.get("wartosc"):
                    extra.append(f"Szacunkowy koszt inwestycji: {body['wartosc']} zł brutto.")
                if body.get("czas"):
                    extra.append(f"Planowany czas realizacji: {body['czas']} miesięcy.")
                if body.get("wojewodztwo"):
                    extra.append(f"Lokalizacja: województwo {body['wojewodztwo']}.")
                body = {"name": f"opis_z_formularza_{datetime.now():%H%M%S}.txt", "b64": base64.b64encode(("\n\n".join([t] + extra)).encode()).decode()}
                path = "/api/test/invest"
            if path in ("/api/test/record", "/api/test/invest"):
                name = re.sub(r"[^\w.\-ąćęłńóśźżĄĆĘŁŃÓŚŹŻ ]", "_", Path(body.get("name") or "plik").name)[:120]
                app.testdir.mkdir(exist_ok=True)
                data = base64.b64decode(body.get("b64") or "")
                f = unique_path(app.testdir / name, data)
                f.write_bytes(data)
                outdir = Path(app.a.out) / "test"
                rel = lambda pth: "/out/test/" + urllib.parse.quote(Path(pth).name)
                try:
                    with LOCK:
                        if path == "/api/test/record":
                            api = ing.Bzp(app.a.url, lambda *x: None)
                            res = record.process(f, app.db, api, outdir, say=lambda *x: None)
                            return self.send(200, {"ok": True, "rekordy": [{"projekt": x["projekt"], "pliki": {k: rel(v) for k, v in x["pliki"].items()},
                                                                             "wynik": json.loads(json.dumps(x["wynik"], default=str))} for x in res]})
                        r = invest.analyze_file(app.db, f)
                        files = invest.save(r, outdir)
                        return self.send(200, {"ok": True, "wynik": json.loads(json.dumps(r, default=str)), "pliki": {k: rel(v) for k, v in files.items()}})
                except Exception as e:
                    sys.stderr.write(traceback.format_exc())
                    return self.send(200, {"ok": False, "blad": f"Nie udało się przeanalizować pliku: {e}"})
            if path == "/api/explain":
                if not llm.config()["enabled"]:
                    return self.send(200, {"ok": False, "blad": "Model językowy jest wyłączony – wyjaśnienie z reguł jest w karcie projektu."})
                rid = body.get("rid")
                p = next((x for x in projects(app.db) if x.get("rid") == rid), None)
                if not p:
                    return self.send(200, {"ok": False, "blad": "Nie ma takiego projektu."})
                try:
                    return self.send(200, {"ok": True, **llm.explain(p)})
                except Exception as e:
                    return self.send(200, {"ok": False, "blad": f"Model nie odpowiedział: {type(e).__name__}"})
            if path == "/api/analyze":
                if not app.bench_count():
                    return self.send(200, {"ok": False, "blad": "Grupa porównawcza jeszcze się buduje – spróbuj za minutę."})
                with LOCK:
                    return self.send(200, bench.analyze(app.db, body))
            if path == "/api/event":
                return self.send(200, edit_event(app, body, who))
            if path == "/api/project":
                return self.send(200, edit_project(app, body, who))
            if path == "/api/link/change":
                return self.send(200, change_link(app, body, who))
            if path == "/api/ai/test":
                return self.send(200, llm.ping())
            if path == "/api/decision/batch":
                try:
                    th = float(body.get("min_pewnosc", 0.9))
                except (TypeError, ValueError):
                    raise HttpError(400, "min_pewnosc musi być liczbą od 0,5 do 1.")
                if not .5 <= th <= 1:
                    raise HttpError(400, "min_pewnosc musi być liczbą od 0,5 do 1.")
                kom = (body.get("komentarz") or "").strip() or f"zatwierdzenie zbiorcze (pewność ≥ {round(th * 100)}%)"
                done = []
                with LOCK:
                    c = app.db.c
                    rows = c.execute("SELECT w.projekt,w.ocid,w.pewnosc,w.metoda FROM powiazania w WHERE w.status='niepewne' AND w.pewnosc>=? "
                                     "AND w.pewnosc=(SELECT MAX(pewnosc) FROM powiazania x WHERE x.projekt=w.projekt)", (th,)).fetchall()
                    if body.get("podglad"):
                        return self.send(200, {"ok": True, "podglad": True, "liczba": len(rows),
                                               "polaczenia": [{"projekt": r[0], "ocid": r[1], "pewnosc": r[2], "metoda": r[3]} for r in rows]})
                    for pid, ocid, pew, met in rows:
                        c.execute("INSERT INTO decyzje VALUES(?,?,?,?,?,?,?,?,?)",
                                  (now(), "Zatwierdzono", f"Połączenie {pid} ↔ {ocid} ({met}, {round(pew * 100)}%)", "kolejka", pid, ocid, None, kom, who))
                        c.execute("UPDATE powiazania SET status='zatwierdzone' WHERE projekt=? AND ocid=?", (pid, ocid))
                        done.append({"projekt": pid, "ocid": ocid, "pewnosc": pew})
                    c.commit()
                return self.send(200, {"ok": True, "zatwierdzono": len(done), "polaczenia": done, "ekspert": who})
            if path == "/api/decision":
                d = {k: body.get(k) for k in ("akcja", "co", "typ", "projekt", "ocid", "kategoria", "komentarz")}
                if not d["akcja"]:
                    raise HttpError(400, "Brak rodzaju decyzji (Zatwierdzono / Poprawiono / Odrzucono).")
                if d["akcja"] == "Odrzucono" and d["typ"] in ("kolejka", "alert", "ted") and not (d["komentarz"] or "").strip():
                    d["komentarz"] = "(bez komentarza)"
                with LOCK:
                    c = app.db.c
                    c.execute("INSERT INTO decyzje VALUES(?,?,?,?,?,?,?,?,?)",
                              (now(), d["akcja"], d["co"], d["typ"], d["projekt"], d["ocid"], d["kategoria"], d["komentarz"], who))
                    st = {"Zatwierdzono": "zatwierdzone", "Odrzucono": "odrzucone", "Poprawiono": "zatwierdzone",
                          "Oznaczono jako wzorcowy": "zatwierdzone"}.get(d["akcja"])
                    if d["typ"] == "kolejka" and d["projekt"] and d["ocid"] and st:
                        c.execute("UPDATE powiazania SET status=? WHERE projekt=? AND ocid=?", (st, d["projekt"], d["ocid"]))
                    if d["typ"] == "alert" and d["kategoria"]:
                        c.execute("UPDATE alerty SET status=? WHERE id=?", ("odrzucony" if d["akcja"] == "Odrzucono" else "przejrzany", d["kategoria"]))
                    if d["typ"] == "ted" and d["kategoria"] and st:
                        c.execute("UPDATE ted SET status=? WHERE numer=?", (st, d["kategoria"]))
                    c.commit()
                return self.send(200, {"ok": True, "ekspert": who})
            return self.send(404, {"blad": "Nie znaleziono."})
    return H


def _txt(v, n=600):
    return re.sub(r"\s+", " ", str(v)).strip()[:n] if v not in (None, "") else ""


def edit_event(app, body: dict, who: str) -> dict:
    klucz = str(body.get("klucz") or "")
    parts = klucz.split("|", 3)
    if len(parts) != 4:
        raise HttpError(400, "Brak identyfikatora zdarzenia.")
    pid, ogl, kod0, pole = parts
    pola = body.get("pola") or {}
    if not isinstance(pola, dict) or not pola:
        raise HttpError(400, "Brak zmian do zapisania.")
    clean = {}
    for k, v in pola.items():
        if k not in ing.EVENT_FIELDS:
            raise HttpError(400, f"Nieznane pole zdarzenia: {k}.")
        if k == "kod":
            v = _txt(v, 20).upper()
            if not re.fullmatch(r"R\d{2,3}(/R\d{2,3})?", v):
                raise HttpError(400, "Kategoria w formacie R07 albo R01/R07.")
        elif k == "skutek_dni" and v not in (None, ""):
            try:
                v = int(float(str(v).replace(",", ".")))
            except ValueError:
                raise HttpError(400, "Skutek w dniach musi być liczbą.")
            if not 0 <= v <= 3650:
                raise HttpError(400, "Skutek w dniach: od 0 do 3650.")
        elif k == "skutek_zl" and v not in (None, ""):
            try:
                v = round(float(str(v).replace(" ", "").replace(",", ".")), 2)
            except ValueError:
                raise HttpError(400, "Skutek w złotych musi być liczbą (może być ujemny).")
        elif k == "skutecznosc" and v not in (None, "") and v not in ing.SKUTECZNOSC:
            raise HttpError(400, "Skuteczność: " + ", ".join(ing.SKUTECZNOSC) + ".")
        elif k == "status" and v not in ("zatwierdzone", "odrzucone", "propozycja – do zatwierdzenia"):
            raise HttpError(400, "Status: zatwierdzone, odrzucone albo propozycja – do zatwierdzenia.")
        else:
            v = _txt(v) if k in ("przyczyna", "dzialanie", "komentarz") else v
        clean[k] = v
    with LOCK:
        db = app.db
        before = {e["klucz"]: e for e in ing.events_detail(db, pid)}.get(klucz)
        if not before:
            raise HttpError(404, "Nie ma takiego zdarzenia (mogło zniknąć po ponownym imporcie).")
        changes = []
        for k, v in clean.items():
            old = (before.get(k) or {}).get("wartosc") if isinstance(before.get(k), dict) else before.get(k)
            if str(old if old is not None else "") == str(v if v is not None else ""):
                continue
            ing.set_korekta(db, "zdarzenie", klucz, k, v, who)
            changes.append(f"{k}: {old if old not in (None, '') else '—'} → {v if v not in (None, '') else '—'}")
        if not changes:
            return {"ok": True, "zmiany": 0}
        ing.apply_event_fixes(db, pid)
        akcja = {"zatwierdzone": "Zatwierdzono zdarzenie", "odrzucone": "Odrzucono zdarzenie"}.get(clean.get("status"), "Poprawiono zdarzenie")
        db.c.execute("INSERT INTO decyzje VALUES(?,?,?,?,?,?,?,?,?)",
                     (now(), akcja, f"{pid} {before['kod']} ({ogl}): " + "; ".join(changes)[:600], "zdarzenie", pid, None, klucz,
                      _txt(body.get("komentarz") or clean.get("komentarz") or "", 400), who))
        db.c.commit()
        after = {e["klucz"]: e for e in ing.events_detail(db, pid)}.get(klucz)
    return {"ok": True, "zmiany": len(changes), "zdarzenie": after, "ekspert": who}


def edit_project(app, body: dict, who: str) -> dict:
    pid = str(body.get("id") or "")
    pola = body.get("pola") or {}
    if not isinstance(pola, dict) or not pola:
        raise HttpError(400, "Brak zmian do zapisania.")
    with LOCK:
        db = app.db
        row = db.c.execute("SELECT " + ",".join(ing.PROJ_FIELDS) + " FROM projekty WHERE id=?", (pid,)).fetchone()
        if not row:
            raise HttpError(404, "Nie ma takiego projektu.")
        cur = dict(zip(ing.PROJ_FIELDS, row))
        clean = {}
        for k, v in pola.items():
            if k not in ing.PROJ_FIELDS:
                raise HttpError(400, f"Nieznane pole projektu: {k}.")
            v = _txt(v, 400)
            if k == "nip" and v and len(ing.digits(v)) != 10:
                raise HttpError(400, "NIP musi mieć 10 cyfr.")
            if k == "nip":
                v = ing.digits(v)
            if k in ("nazwa", "beneficjent") and not v:
                raise HttpError(400, "Nazwa projektu i odbiorcy nie mogą być puste.")
            clean[k] = v
        changes, relink = [], False
        for k, v in clean.items():
            if str(cur[k] or "") == v:
                continue
            ing.set_korekta(db, "projekt", pid, k, v, who)
            db.c.execute(f"UPDATE projekty SET {k}=? WHERE id=?", (v or None, pid))
            changes.append(f"{k}: {cur[k] or '—'} → {v or '—'}")
            relink |= k in ("nip", "id_publiczny", "beneficjent")
        if not changes:
            return {"ok": True, "zmiany": 0}
        db.c.execute("INSERT INTO decyzje VALUES(?,?,?,?,?,?,?,?,?)",
                     (now(), "Poprawiono rekord projektu", f"{pid}: " + "; ".join(changes)[:600], "projekt", pid, None, None,
                      _txt(body.get("komentarz") or "", 400), who))
        db.c.commit()
        note = None
        if relink:
            api = ing.Bzp(app.a.url, lambda *x: None)
            if api.check():
                links = ing.reprocess(db, api, pid)
                note = f"Ponownie połączono z BZP: {links[0]['status'] if links else 'brak'} ({links[0]['metoda'] if links else '—'})."
            else:
                note = "BZP niedostępne – zmiana zapisana, połączenie zostanie odświeżone przy następnym imporcie."
    return {"ok": True, "zmiany": len(changes), "uwaga": note, "ekspert": who}


def change_link(app, body: dict, who: str) -> dict:
    pid, cel = str(body.get("projekt") or ""), _txt(body.get("cel") or "", 80)
    with LOCK:
        db = app.db
        rec = ing.rec_from_db(db, pid)
        if rec is None:
            raise HttpError(404, "Nie ma takiego projektu.")
        old = db.c.execute("SELECT ocid FROM powiazania WHERE projekt=? ORDER BY pewnosc DESC", (pid,)).fetchone()
        api = ing.Bzp(app.a.url, lambda *x: None)
        if not api.check():
            raise HttpError(503, "BZP jest niedostępne – nie da się teraz sprawdzić wskazanego ogłoszenia. Spróbuj za chwilę.")
        if cel:
            try:
                found = ing.link_manual(api, rec, cel)
            except ValueError as e:
                raise HttpError(400, str(e))
            if not found:
                raise HttpError(404, f"Nie znaleziono w BZP postępowania dla „{cel}”. Sprawdź numer – poprzednie powiązanie zostaje bez zmian.")
        ing.set_korekta(db, "powiazanie", pid, "cel", cel, who)
        links = ing.reprocess(db, api, pid)
        new = links[0]["ocid"] if links else None
        db.c.execute("INSERT INTO decyzje VALUES(?,?,?,?,?,?,?,?,?)",
                     (now(), "Zmieniono powiązanie" if cel else "Przywrócono powiązanie automatyczne",
                      f"{pid}: {(old[0] if old else None) or 'brak'} → {new or 'brak'}" + (f" (wskazano: {cel})" if cel else ""),
                      "kolejka", pid, new, None, _txt(body.get("komentarz") or "", 400), who))
        db.c.commit()
        nowy = next((x for x in projects(db) if x["rid"] == pid), None)
    return {"ok": True, "ocid": new, "powiazanie": links[0] if links else None, "projekt": nowy, "ekspert": who}


def save_subsectors(val) -> dict:
    if not isinstance(val, dict) or not isinstance(val.get("reguly"), list) or not val["reguly"]:
        raise HttpError(400, "Podsektory: oczekiwana lista reguł {nazwa, wzorce}.")
    rules = []
    for r in val["reguly"]:
        nazwa = _txt((r or {}).get("nazwa"), 80)
        wz = [str(w).strip().lower() for w in (r or {}).get("wzorce", []) if str(w).strip()]
        if not nazwa or not wz:
            raise HttpError(400, "Każdy podsektor potrzebuje nazwy i co najmniej jednego wzorca.")
        for w in wz:
            try:
                re.compile(w)
            except re.error as e:
                raise HttpError(400, f"Niepoprawny wzorzec „{w}” w podsektorze {nazwa}: {e}.")
        rules.append({"nazwa": nazwa, "wzorce": wz[:30]})
    f = cfg.DIR / "podsektory.json"
    try:
        cur = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        cur = {}
    cur.update({"reguly": rules, "domyslny": _txt(val.get("domyslny") or cur.get("domyslny") or "Inne obiekty wod-kan", 80)})
    f.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cfg.reload()
    return {"reguly": rules, "domyslny": cur["domyslny"]}


DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def safe_next(n: str) -> str:
    return n if n.startswith("/") and not n.startswith("//") else "/app"


def unique_path(f: Path, data: bytes) -> Path:
    if not f.exists() or f.read_bytes() == data:
        return f
    for i in range(2, 1000):
        g = f.with_name(f"{f.stem}_{i}{f.suffix}")
        if not g.exists() or g.read_bytes() == data:
            return g
    return f.with_name(f"{f.stem}_{uuid.uuid4().hex[:6]}{f.suffix}")


def lan_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description="BidRadar – serwer aplikacji")
    ap.add_argument("--host", default=os.environ.get("RM_HOST", "127.0.0.1"), help="0.0.0.0 = dostęp z sieci (VM, kontener)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("RM_PORT", 8000)))
    ap.add_argument("--xlsx", default=os.environ.get("RM_XLSX", "paczka/Risk_Miner_dane_projekty.xlsx"))
    ap.add_argument("--url", default=os.environ.get("RM_BZP_URL") or cfg.file("zrodla").get("bzp", {}).get("url", "http://127.0.0.1:8010"))
    default_db = "bidradar.db" if Path("bidradar.db").exists() or not Path("risk_miner.db").exists() else "risk_miner.db"
    ap.add_argument("--db", default=os.environ.get("RM_DB", default_db))
    ap.add_argument("--out", default=os.environ.get("RM_OUT", "out"))
    ap.add_argument("--bez-importu", action="store_true", help="nie importuj przy starcie (np. testy)")
    a = ap.parse_args()
    app = App(a)
    made = auth.bootstrap(app.db.c, Path(a.db).resolve().parent) if AUTH_ON else []
    if not a.bez_importu:
        try:
            want = {r.get("Identyfikator rekordu") for r in ing.read_xlsx(Path(a.xlsx))}
            have = {r[0] for r in app.db.c.execute("SELECT id FROM projekty")}
            missing = bool(want - have)
        except Exception:
            missing = not app.db.c.execute("SELECT COUNT(*) FROM projekty").fetchone()[0]
        if missing:
            print("Importuję projekty z arkusza i BZP…")
            r = app.refresh(with_ted=False)
            print("  " + (f"gotowe: {r['projekty']} projektów, {r['zdarzenia']} zdarzeń" if r["ok"] else "BŁĄD: " + r["blad"]))
        if app.bench_state != "gotowa":
            print("Buduję w tle grupę porównawczą (~27 tys. umów z BZP, 1–3 min)" + (" – nowa wersja reguł lub pól…" if app.bench_count() else "…"))
            threading.Thread(target=app.build_bench, daemon=True).start()
        n = app.process_docs()
        if n:
            print(f"Przetworzono dokumenty z folderu dokumenty/: {n}")
        if (cfg.get(app.db.c, "zrodla").get("ted") or {}).get("wlaczone", True) and not os.environ.get("RM_OFFLINE"):
            threading.Thread(target=app.refresh_ted, daemon=True).start()
        print("Geokoduję siedziby zamawiających (pierwszy raz do ~30 s)…")
        projects(app.db)
    hours = float(os.environ.get("RM_MONITOR_H") or (cfg.get(app.db.c, "zrodla").get("monitoring") or {}).get("co_ile_godzin") or 0)
    if hours > 0:
        threading.Thread(target=app.monitor_loop, args=(hours,), daemon=True).start()
    for port in range(a.port, a.port + 20):
        try:
            srv = ThreadingHTTPServer((a.host, port), handler(app))
            srv.daemon_threads = True
            break
        except OSError:
            continue
    else:
        raise SystemExit(f"Porty {a.port}–{a.port + 19} są zajęte. Podaj inny: --port 9000")
    shown = "127.0.0.1" if a.host in ("0.0.0.0", "") else a.host
    print(f"\nBidRadar działa: http://{shown}:{port}   (aplikacja: http://{shown}:{port}/app · zatrzymanie: Ctrl+C)")
    if a.host in ("0.0.0.0", "") and lan_ip():
        print(f"  z innych komputerów w sieci: http://{lan_ip()}:{port}")
    if not AUTH_ON:
        print("  Logowanie wyłączone (RM_AUTH=off) – wszystkie zmiany zapisują się jako „użytkownik lokalny”.")
    elif made:
        print("  Konta startowe (przeglądanie działa bez logowania; logowanie jest potrzebne do decyzji eksperta):")
        for login, pw, rola in made:
            print(f"    login: {login:<9} hasło: {pw}   ({auth.ROLES[rola]})")
        if not os.environ.get("RM_DEMO_PASSWORD"):
            print(f"  Hasła zapisano też w pliku {Path(a.db).resolve().parent / 'hasla_startowe.txt'}. Zmiana: python3 rm_auth.py haslo ekspert")
    if hours > 0:
        print(f"  Monitorowanie projektów w toku: co {hours:g} h (zmiana: RM_MONITOR_H, 0 = wyłączone)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
