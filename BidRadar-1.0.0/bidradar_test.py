#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rm_ingest as ing
import rm_benchmark as bm
import rm_invest
import rm_record

TABULAR = {".xlsx", ".xlsm", ".csv", ".tsv", ".json"}


def kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in TABULAR:
        return "rekord"
    try:
        grid = rm_record.raw_grid(path)
        recs, _ = rm_record.records_from_grid(grid)
    except Exception:
        recs = []
    try:
        _, units, _ = rm_invest.read_text(path)
        words = sum(len(t.split()) for _, t in units)
    except Exception:
        words = 0
    if recs and len(recs[0]) >= 3 and words < 600:
        return "rekord"
    return "inwestycja"


def main():
    ap = argparse.ArgumentParser(description="BidRadar – test: rekord projektu + opis nowej inwestycji")
    ap.add_argument("pliki", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("wyniki_testu"))
    ap.add_argument("--url", default=os.environ.get("RM_BZP_URL", "http://127.0.0.1:8010"))
    default_db = HERE / ("bidradar.db" if (HERE / "bidradar.db").exists() or not (HERE / "risk_miner.db").exists() else "risk_miner.db")
    ap.add_argument("--db", type=Path, default=Path(os.environ.get("RM_DB", default_db)))
    a = ap.parse_args()
    t0 = time.time()
    for f in a.pliki:
        if not f.exists():
            sys.exit(f"Nie ma pliku: {f}")
    db = ing.Db(a.db)
    db.c.executescript(bm.SCHEMA)
    api = ing.Bzp(a.url, lambda op, adr, wyn: db.put("log", {"czas": time.strftime("%Y-%m-%dT%H:%M:%S"), "operacja": op, "adres": adr, "wynik": str(wyn)}, replace=False))
    print(f"BidRadar – test · baza: {a.db} · BZP: {a.url}")
    bzp_ok = api.check()
    print(("  ✓ kopia BZP odpowiada" if bzp_ok else f"  ✗ kopia BZP nie odpowiada ({api.last_error}) – rekord nie zostanie połączony; analiza inwestycji bez porównania z BZP"))
    if bzp_ok and bm.stale(db):
        print("  … buduję grupę porównawczą z ogłoszeń o wykonaniu umowy (jednorazowo, ok. 30–60 s)")
        n = bm.build(db, api, say=lambda *x: None)
        print(f"  ✓ grupa porównawcza: {n} umów")
    a.out.mkdir(parents=True, exist_ok=True)
    made = []
    for f in a.pliki:
        k = kind(f)
        print(f"\n▶ {f.name}: rozpoznano jako {'REKORD PROJEKTU' if k == 'rekord' else 'OPIS NOWEJ INWESTYCJI'}")
        try:
            if k == "rekord":
                if not bzp_ok:
                    raise RuntimeError("Do importu rekordu potrzebna jest działająca kopia BZP (bzp_mirror.py).")
                for r in rm_record.process(f, db, api, a.out, say=lambda s: print("  " + s)):
                    made += list(r["pliki"].values())
            else:
                r = rm_invest.analyze_file(db, f)
                p = rm_invest.save(r, a.out)
                s = r["podsumowanie"]
                print(f"  {s['nazwa'][:90]}")
                print(f"  gotowość opisu: {s['gotowosc_pct']}% · ocena ryzyka: {s['ocena_ryzyka']} · ryzyka: " + ", ".join(x.split(' ')[0] for x in s["najwazniejsze_ryzyka"]))
                print(f"  eksport: {p['md']}")
                made += list(p.values())
        except Exception as e:
            print(f"  ✗ błąd: {e}")
    print(f"\nGotowe w {time.time() - t0:.0f} s. Pliki do oddania ({a.out.resolve()}):")
    for m in made:
        print("  " + m)


if __name__ == "__main__":
    main()
