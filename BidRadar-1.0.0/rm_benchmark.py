#!/usr/bin/env python3
from __future__ import annotations

import json
import time
import math
import os
import re
import sys
from datetime import date
from pathlib import Path

import rm_config as cfg
import rm_ingest as ing

PROV = {"PL02": "dolnośląskie", "PL04": "kujawsko-pomorskie", "PL06": "lubelskie", "PL08": "lubuskie", "PL10": "łódzkie",
        "PL12": "małopolskie", "PL14": "mazowieckie", "PL16": "opolskie", "PL18": "podkarpackie", "PL20": "podlaskie",
        "PL22": "pomorskie", "PL24": "śląskie", "PL26": "świętokrzyskie", "PL28": "warmińsko-mazurskie",
        "PL30": "wielkopolskie", "PL32": "zachodniopomorskie"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS umowy_sektor(numer TEXT PRIMARY KEY, ocid TEXT, object_id TEXT, data_publikacji TEXT,
  wojewodztwo TEXT, zamawiajacy TEXT, cpv TEXT, przedmiot TEXT, podsektor TEXT, ziw INT, data_zawarcia TEXT,
  termin_planowany TEXT, koniec TEXT, wartosc REAL, wyplacono REAL, zmiana_pct REAL, w_terminie INT, nalezycie INT,
  liczba_zmian INT, opoznienie_mies REAL, czas_mies REAL);
CREATE TABLE IF NOT EXISTS analizy(czas TEXT, opis TEXT, wynik TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


BENCH_V = "2-" + cfg.rules_hash()


def subsector(name: str) -> str:
    from_cfg = cfg.subsector(name)
    if from_cfg:
        return from_cfg
    n = (name or "").lower()
    if "oczyszczal" in n:
        return "Oczyszczalnia ścieków"
    if "deszczow" in n or "retenc" in n or "odwodni" in n:
        return "Kanalizacja deszczowa i retencja"
    if "uzdatnian" in n or re.search(r"\bsuw\b|stacj\w* uzdat|ujęci|zbiornik\w* wody|studni", n):
        return "Uzdatnianie i zaopatrzenie w wodę"
    if "kanalizac" in n or "tłoczn" in n or "ścieków" in n or "przepompowni" in n or re.search(r"wodno-ściek|ściekow|skanalizow", n):
        return "Kanalizacja sanitarna"
    if "wodociąg" in n or "sieci wod" in n or "wody" in n:
        return "Sieć wodociągowa"
    return "Inne obiekty wod-kan"


def months(a: str | None, b: str | None) -> float | None:
    try:
        d1, d2 = date.fromisoformat(a[:10]), date.fromisoformat(b[:10])
        return round((d2 - d1).days / 30.44, 1)
    except Exception:
        return None


def build(db: ing.Db, api: ing.Bzp, say=print) -> int:
    db.c.executescript(SCHEMA)
    after, n, page = None, 0, 0
    while True:
        p = {"NoticeType": "ContractPerformingNotice", "PageSize": 100}
        if after:
            p["SearchAfter"] = after
        url = api.base + "?" + ing.urllib.parse.urlencode(p)
        try:
            with api.opener.open(url, timeout=120) as r:
                items = json.load(r)
        except Exception as e:
            say(f"  błąd strony {page}: {e}")
            break
        for attempt in range(5):
            try:
                n += _page(db, items)
                db.c.commit()
                break
            except ing.sqlite3.OperationalError as e:
                db.c.rollback()
                if "locked" not in str(e) or attempt == 4:
                    raise
                time.sleep(3 * (attempt + 1))
        page += 1
        if page % 20 == 0:
            say(f"  … {n} umów")
        if len(items) < 100:
            break
        after = items[-1]["objectId"]
    if n:
        db.c.execute("INSERT OR REPLACE INTO meta VALUES('bench_v', ?)", (BENCH_V,))
        db.c.commit()
    return n


def _page(db: ing.Db, items: list) -> int:
    n = 0
    for it in items:
        s = ing.text(it.get("htmlBody"))
        wu = ing.money(ing.field(r"4\.4\.\) Wartość umowy[^:]*:\s*([\d\s,\.]+)", s))
        wy = ing.money(ing.field(r"5\.5\.\)[^:]*:\s*([\d\s,\.]+)", s))
        sign = ing.field(r"4\.1\.\) Data zawarcia umowy[^:]*:\s*(\d{4}-\d{2}-\d{2})", s)
        plan = ing.plan_date(s, sign)
        end = ing.field(r"5\.2\.\)[^:]*:\s*(\d{4}-\d{2}-\d{2})", s)
        term = ing.field(r"5\.3\.\)[^:]*:\s*(\S+)", s).lower()
        nal = ing.field(r"5\.6\.\)[^:]*:\s*(\S+)", s).lower()
        pct = round((wy - wu) / wu * 100, 1) if wu and wy and wu > 10000 else None
        if pct is not None and abs(pct) > 200:
            pct = None
        delay = months(plan, end) if plan and end else None
        obj = it.get("orderObject") or ""
        db.c.execute("INSERT OR REPLACE INTO umowy_sektor VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            it["noticeNumber"], it.get("tenderId"), it.get("objectId"), it.get("publicationDate"),
            PROV.get(it.get("organizationProvince") or "", ""), it.get("organizationName"), (it.get("cpvCode") or "")[:10],
            obj, subsector(obj), int(bool(re.search(r"zaprojektuj|projektuj i wybuduj|PFU|program\w* funkcjonaln", obj + s[:4000], re.I))),
            sign or None, plan or None, end or None, wu, wy, pct,
            None if not term else int(term.startswith("tak")), None if not nal else int(nal.startswith("tak")),
            int(ing.field(r"5\.4\.1\.\)[^:]*:\s*(\d+)", s) or 0), max(delay, 0) if delay is not None else None,
            months(sign, end) if sign and end else None))
        n += 1
    return n


def stale(db: ing.Db) -> bool:
    db.c.executescript(SCHEMA)
    if not db.c.execute("SELECT COUNT(*) FROM umowy_sektor").fetchone()[0]:
        return True
    r = db.c.execute("SELECT v FROM meta WHERE k='bench_v'").fetchone()
    return not r or r[0] != BENCH_V


def pctl(vals: list[float], q: float) -> float | None:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q
    f, c = math.floor(k), math.ceil(k)
    return round(v[f] + (v[c] - v[f]) * (k - f), 1)


def words(s: str) -> set[str]:
    stop = {"budowa", "rozbudowa", "przebudowa", "oraz", "wraz", "gmina", "gminy", "gminie", "miejscowości", "ulicy", "terenie",
            "zadania", "zadanie", "realizacja", "wykonanie", "zakresie", "zamówienia", "robót", "budowlanych", "części", "nr"}
    return {w[:7] for w in re.findall(r"[a-ząćęłńóśźż]{4,}", (s or "").lower()) if w not in stop}


def features(opis: str, val: float | None, months_plan: float | None, woj: str | None) -> dict:
    low = (opis or "").lower()
    km = re.search(r"(\d+(?:[,.]\d+)?)\s*km", low)
    rlm = re.search(r"(\d[\d\s]*)\s*rlm", low)
    return {"podsektor": subsector(opis), "ziw": bool(re.search(r"zaprojektuj|pfu|funkcjonaln", low)),
            "dlugosc_km": float(km.group(1).replace(",", ".")) if km else None,
            "rlm": int(re.sub(r"\D", "", rlm.group(1))) if rlm else None, "wartosc": val, "czas_mies": months_plan,
            "wojewodztwo": woj, "geotechnika": bool(re.search(r"geotechn|badan\w* (gruntu|podłoża)|warunk\w* gruntow", low)),
            "nieruchomosci": bool(re.search(r"dysponowani|nieruchomo|służebno|grunt\w* prywat|działk", low)),
            "uzgodnienia": bool(re.search(r"decyzj|pozwoleni|uzgodni|środowisk", low)), "slowa": words(opis)}


def analyze(db: ing.Db, req: dict) -> dict:
    db.c.executescript(SCHEMA)
    total = db.c.execute("SELECT COUNT(*) FROM umowy_sektor").fetchone()[0]
    if not total:
        return {"ok": False, "blad": "Grupa porównawcza jeszcze się buduje – spróbuj za chwilę."}
    def num(x):
        try:
            return float(re.sub(r"[^\d,\.]", "", str(x)).replace(",", ".")) if x not in (None, "") else None
        except ValueError:
            return None
    f = features(req.get("opis", ""), num(req.get("wartosc")), num(req.get("czas")), req.get("wojewodztwo"))
    for k, v in (req.get("cechy") or {}).items():
        if v not in (None, "", set()):
            f[k] = v
    cols = "numer,ocid,object_id,wojewodztwo,zamawiajacy,przedmiot,podsektor,ziw,wartosc,wyplacono,zmiana_pct,w_terminie,nalezycie,liczba_zmian,opoznienie_mies,czas_mies,data_zawarcia"
    rows = [dict(zip(cols.split(","), r)) for r in db.c.execute(f"SELECT {cols} FROM umowy_sektor WHERE podsektor=?", (f["podsektor"],))]
    scope = f"podsektor „{f['podsektor']}”, cała Polska"
    group = rows
    if f["wartosc"]:
        band = [r for r in rows if r["wartosc"] and f["wartosc"] / 3 <= r["wartosc"] <= f["wartosc"] * 3]
        if len(band) >= 30:
            group, scope = band, scope + f", wartość umowy {fmt_mln(f['wartosc'] / 3)}–{fmt_mln(f['wartosc'] * 3)}"
    if f["wojewodztwo"]:
        reg = [r for r in group if r["wojewodztwo"] == f["wojewodztwo"]]
        if len(reg) >= 30:
            group, scope = reg, scope.replace("cała Polska", f"woj. {f['wojewodztwo']}")

    cost = [r["zmiana_pct"] for r in group if r["zmiana_pct"] is not None]
    late_known = [r for r in group if r["w_terminie"] is not None]
    delays = [r["opoznienie_mies"] for r in group if r["w_terminie"] == 0 and r["opoznienie_mies"] and r["opoznienie_mies"] > 0]
    dur = [r["czas_mies"] for r in group if r["czas_mies"]]
    stats = {"zakres": scope, "n_umow": len(group), "n_sektor": total,
             "koszt": {"n": len(cost), "p25": pctl(cost, .25), "mediana": pctl(cost, .5), "p75": pctl(cost, .75), "p90": pctl(cost, .9),
                       "udzial_powyzej_15": round(sum(c > 15 for c in cost) / len(cost) * 100) if cost else None,
                       "udzial_wzrost": round(sum(c > 0 for c in cost) / len(cost) * 100) if cost else None},
             "termin": {"n": len(late_known), "udzial_po_terminie": round(sum(not r["w_terminie"] for r in late_known) / len(late_known) * 100) if late_known else None,
                        "n_opoznien": len(delays), "p25": pctl(delays, .25), "mediana": pctl(delays, .5), "p75": pctl(delays, .75)},
             "czas_realizacji": {"n": len(dur), "mediana": pctl(dur, .5), "p75": pctl(dur, .75)},
             "nienalezyte_pct": round(sum(r["nalezycie"] == 0 for r in group) / max(1, sum(r["nalezycie"] is not None for r in group)) * 100, 1)}

    W = cfg.weights(db.c)
    stats["wagi_podobienstwa"] = {k: round(v, 3) for k, v in W.items()}
    sim = []
    for r in group if len(group) < 4000 else rows:
        w = words(r["przedmiot"])
        common = f["slowa"] & w
        jac = len(common) / len(f["slowa"] | w) if f["slowa"] and w else 0
        sv = 1 - min(1, abs(math.log((r["wartosc"] or 1) / f["wartosc"])) / math.log(5)) if f["wartosc"] and r["wartosc"] else .3
        sr = 1 if f["wojewodztwo"] and r["wojewodztwo"] == f["wojewodztwo"] else 0
        sz = 1 if bool(r["ziw"]) == f["ziw"] else 0
        score = W["tekst"] * jac + W["wartosc"] * sv + W["region"] * sr + W["formula"] * sz
        if r["zmiana_pct"] is None:
            continue
        why = []
        if common:
            why.append("wspólne cechy przedmiotu: " + ", ".join(sorted(common)[:4]))
        if f["wartosc"] and r["wartosc"]:
            why.append(f"wartość {fmt_mln(r['wartosc'])} (Twoja: {fmt_mln(f['wartosc'])})")
        if sr:
            why.append("to samo województwo")
        if sz and f["ziw"]:
            why.append("formuła zaprojektuj i wybuduj / PFU")
        sim.append((round(score, 2), r, why))
    sim.sort(key=lambda x: -x[0])
    similar = [{"podobienstwo": s, "numer": r["numer"], "url": ing.NOTICE_URL.format(r["object_id"]), "zamawiajacy": r["zamawiajacy"],
                "przedmiot": r["przedmiot"], "wojewodztwo": r["wojewodztwo"], "wartosc": r["wartosc"], "zmiana_pct": r["zmiana_pct"],
                "w_terminie": r["w_terminie"], "opoznienie_mies": r["opoznienie_mies"], "dlaczego": why} for s, r, why in sim[:6]]

    flags, recs = [], []
    if f["ziw"]:
        z = [r for r in rows if r["ziw"] and r["zmiana_pct"] is not None]
        o = [r for r in rows if not r["ziw"] and r["zmiana_pct"] is not None]
        share = lambda xs, cond: round(sum(cond(x) for x in xs) / len(xs) * 100) if xs else 0
        sz_, so_ = share(z, lambda r: r["zmiana_pct"] > 0), share(o, lambda r: r["zmiana_pct"] > 0)
        cz, co = share(z, lambda r: r["liczba_zmian"] > 0), share(o, lambda r: r["liczba_zmian"] > 0)
        riskier = sz_ - so_ >= 3 or cz - co >= 5
        flags.append({"kod": "R01", "pewnosc": "średnia" if riskier else "niska",
                      "tekst": f"Formuła zaprojektuj i wybuduj / PFU: wzrost kosztu w {sz_}% takich umów wobec {so_}% pozostałych; "
                               f"zmiany umowy w {cz}% wobec {co}% (n = {len(z)} i {len(o)}, podsektor). "
                               + ("Ryzyko zakresu jest tu wyraźnie wyższe." if riskier else "Dane BZP nie pokazują wyraźnie wyższego ryzyka – zakres i tak zależy od jakości PFU."),
                      "zrodlo": "umowy_sektor: pola 4.4, 5.4.1 i 5.5 ogłoszeń o wykonaniu umowy"})
        recs.append("Doprecyzuj zakres w PFU (ilości, parametry, granice robót) i dołącz inwentaryzację stanu istniejącego.")
    if not f["geotechnika"]:
        flags.append({"kod": "R02", "pewnosc": "niska", "tekst": "Opis nie wspomina o badaniach geotechnicznych ani rozpoznaniu warunków gruntowych.",
                      "zrodlo": "reguła na treści opisu"})
        recs.append("Wykonaj badania podłoża przed przetargiem i udostępnij je wykonawcom w dokumentacji.")
    if f["dlugosc_km"] and not f["nieruchomosci"]:
        flags.append({"kod": "R03", "pewnosc": "niska", "tekst": f"Sieć ok. {pl(f['dlugosc_km'])} km, a opis nie wspomina o prawie do dysponowania nieruchomościami na trasie.",
                      "zrodlo": "reguła na treści opisu"})
        recs.append("Potwierdź prawo do dysponowania gruntem na całej trasie (służebności, zgody właścicieli) przed ogłoszeniem.")
    md = stats["czas_realizacji"]["mediana"]
    if f["czas_mies"] and md and f["czas_mies"] < md:
        flags.append({"kod": "R06", "pewnosc": "średnia", "tekst": f"Planowany czas {pl(f['czas_mies'])} mies. jest krótszy niż mediana rzeczywistego czasu realizacji "
                      f"w grupie porównawczej: {pl(md)} mies. (n = {stats['czas_realizacji']['n']}).", "zrodlo": "umowy_sektor: pola 4.1 i 5.2"})
    if stats["termin"]["udzial_po_terminie"] and stats["termin"]["udzial_po_terminie"] >= 30:
        recs.append(f"Zaplanuj rezerwę czasu ok. {pl(stats['termin']['p75'] or stats['termin']['mediana'] or 0)} mies. – "
                    f"{stats['termin']['udzial_po_terminie']}% podobnych umów skończyło się po terminie (75. percentyl opóźnienia).")
    if stats["koszt"]["p75"] is not None:
        recs.append(f"Zarezerwuj w budżecie ok. {pl(max(stats['koszt']['p75'], 0))}% ponad wartość umowy (75. percentyl zmiany kosztu, n = {stats['koszt']['n']}).")
    clauses = ["Klauzula zmian umowy (art. 455 ust. 1 pkt 1 Pzp) z opisem robót zamiennych i limitem wartości.",
               "Waloryzacja wynagrodzenia oparta na wskaźniku GUS, z progiem i limitem.",
               "Harmonogram z kamieniami milowymi (projekt, decyzje, roboty, rozruch) i jasnymi kryteriami odbioru."]
    if f["podsektor"] in ("Oczyszczalnia ścieków", "Uzdatnianie i zaopatrzenie w wodę"):
        clauses.append("Program prób i rozruchu z parametrami gwarantowanymi i procedurą przy ich nieosiągnięciu (R10/R15).")
    out = {"ok": True, "cechy": {k: v for k, v in f.items() if k != "slowa"}, "statystyka": stats, "podobne": similar,
           "flagi": flags, "rekomendacje": recs, "zapisy_umowne": clauses}
    db.c.execute("INSERT INTO analizy VALUES(datetime('now'),?,?)", (req.get("opis", ""), json.dumps(out, ensure_ascii=False)))
    db.c.commit()
    return out


def pl(v) -> str:
    return f"{v:g}".replace(".", ",") if isinstance(v, (int, float)) else str(v)


def fmt_mln(v: float | None) -> str:
    return "—" if not v else f"{v / 1e6:.1f} mln zł".replace(".", ",")


if __name__ == "__main__":
    db = ing.Db(Path(os.environ.get("RM_DB", "bidradar.db")))
    api = ing.Bzp(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RM_BZP_URL", "http://127.0.0.1:8010"), lambda *a: None)
    if not api.check():
        sys.exit("Brak odpowiedzi z API BZP: " + api.last_error)
    print("Buduję grupę porównawczą z ogłoszeń o wykonaniu umowy…")
    n = build(db, api)
    print(f"Gotowe: {n} umów w tabeli umowy_sektor.")
    r = analyze(db, {"opis": "Budowa kanalizacji sanitarnej ok. 12 km z 3 przepompowniami, zaprojektuj i wybuduj na podstawie PFU", "wartosc": "6000000", "czas": "14"})
    print(json.dumps(r["statystyka"], ensure_ascii=False, indent=1))
