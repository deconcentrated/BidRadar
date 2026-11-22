#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(HERE))
import rm_ingest as ing
import rm_benchmark as bm
import rm_docs as docs
import rm_invest
import rm_record
import rm_llm as llm


def find_xlsx() -> Path | None:
    for base in (HERE, Path.home(), Path("/home"), Path("/root")):
        for f in base.rglob("Risk_Miner_dane_projekty.xlsx"):
            return f
    return None


def pct(a, b):
    return f"{round(a / b * 100)}%" if b else "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", type=Path)
    ap.add_argument("--url", default=os.environ.get("RM_BZP_URL", "http://127.0.0.1:8010"))
    ap.add_argument("--out", type=Path, default=HERE / "raport_testu.md")
    a = ap.parse_args()
    xlsx = a.xlsx or find_xlsx()
    if not xlsx or not xlsx.exists():
        sys.exit("Nie znaleziono Risk_Miner_dane_projekty.xlsx – podaj --xlsx")
    tmp = Path(tempfile.mkdtemp(prefix="bidradar_eval_"))
    dbp, outd = tmp / "eval.db", tmp / "out"
    T = {}
    print("1/5 import arkusza i wzbogacenie z BZP…")
    t = time.time()
    res = ing.run(xlsx, a.url, dbp, outd, say=lambda *x, **k: None)
    T["import"] = time.time() - t
    db = ing.Db(dbp)
    api = ing.Bzp(a.url, lambda *x: None)
    print("2/5 grupa porównawcza…")
    t = time.time()
    nb = bm.build(db, api, say=lambda *x: None)
    T["bench"] = time.time() - t
    c = db.c
    print("3/5 dokumenty…")
    db.c.executescript(docs.SCHEMA)
    t = time.time()
    dres = []
    for f in sorted((HERE / "dokumenty").glob("*")):
        if f.suffix.lower() in (".docx", ".pdf", ".txt"):
            try:
                dres.append(docs.process(db, f, say=lambda *x: None))
            except Exception as e:
                dres.append({"blad": str(e), "nazwa": f.name})
    T["docs"] = time.time() - t
    print("4/5 przykładowa nowa inwestycja…")
    ex = HERE / "przyklady_testu"
    inv = rec = None
    t = time.time()
    if (ex / "przyklad_opis_inwestycji.docx").exists():
        inv = rm_invest.analyze_file(db, ex / "przyklad_opis_inwestycji.docx")
    T["invest"] = time.time() - t
    print("5/5 przykładowy rekord…")
    t = time.time()
    if (ex / "przyklad_rekord.xlsx").exists():
        rec = rm_record.process(ex / "przyklad_rekord.xlsx", db, api, tmp / "rek", say=lambda *x: None)[0]["wynik"]
    T["record"] = time.time() - t

    projs = c.execute("SELECT id, nazwa, beneficjent, rodzaj_id, id_publiczny FROM projekty WHERE id IN (SELECT id FROM projekty) ORDER BY lp").fetchall()
    ids_xlsx = {r["Identyfikator rekordu"] for r in ing.read_xlsx(xlsx)}
    projs = [p for p in projs if p[0] in ids_xlsx]
    best = {}
    for pid, *_ in projs:
        r = c.execute("SELECT status, metoda, pewnosc, ocid, podstawa FROM powiazania WHERE projekt=? ORDER BY pewnosc DESC LIMIT 1", (pid,)).fetchone()
        best[pid] = r
    st = Counter(r[0] if r else "brak" for r in best.values())
    met = Counter(r[1] if r else "brak" for r in best.values())
    nip_ok = sum(1 for r in best.values() if r and "NIP zgodny" in (r[4] or ""))
    bzp_num = [p for p in projs if ing.bzp_base(p[4])]
    kpo = [p for p in projs if "kpo" in (p[3] or "").lower()]
    notices = Counter(t for (t,) in c.execute("SELECT typ FROM ogloszenia"))
    umowy = c.execute("SELECT COUNT(*), SUM(wartosc), SUM(wyplacono) FROM umowy").fetchone()
    late = c.execute("SELECT COUNT(*) FROM umowy WHERE lower(w_terminie) LIKE 'nie%'").fetchone()[0]
    ev = Counter(k for (k,) in c.execute("SELECT kod FROM zdarzenia"))
    dup = c.execute("SELECT COUNT(*) FROM postepowania WHERE uwagi IS NOT NULL AND uwagi!='[]'").fetchone()[0]
    gaps = sum(1 for r in best.values() if r and "odwołania" in (r[1] or ""))
    cov = c.execute("""SELECT COUNT(*), SUM(wartosc IS NOT NULL), SUM(wyplacono IS NOT NULL), SUM(termin_planowany IS NOT NULL),
                       SUM(koniec IS NOT NULL), SUM(w_terminie IS NOT NULL), SUM(zmiana_pct IS NOT NULL) FROM umowy_sektor""").fetchone()
    subs = Counter(s for (s,) in c.execute("SELECT podsektor FROM umowy_sektor"))
    ver = Counter(w for (w,) in c.execute("SELECT wynik FROM weryfikacja"))
    frag = c.execute("SELECT COUNT(*) FROM fragmenty").fetchone()[0]

    L = [f"# BidRadar – raport z testu działania", "",
         f"*Wygenerowano {datetime.now():%Y-%m-%d %H:%M} skryptem `bidradar_eval.py` na świeżej bazie · BZP: {a.url} · tryb analizy: {llm.status()['tryb']}*", "",
         "## 1. Podsumowanie", "",
         f"- **Projekty z arkusza MFiPR: {len(projs)}** – wzbogaconych automatycznie z BZP w **{T['import']:.0f} s** ({res['zapytania']} zapytań do API, bez ręcznych wpisów).",
         f"- **{st.get('poprawne', 0)} z {len(projs)}** połączonych automatycznie z podwójnym potwierdzeniem (numer ogłoszenia **i** NIP zamawiającego); "
         f"**{st.get('niepewne', 0)}** przekazanych do decyzji eksperta z uzasadnieniem; **{st.get('brak', 0)}** bez dopasowania.",
         f"- Z {len(bzp_num)} projektów z numerem BZP: {sum(1 for p in bzp_num if best[p[0]] and best[p[0]][0] in ('poprawne', 'zatwierdzone'))} połączone poprawnie, "
         f"{gaps} odtworzone z odwołań w innych ogłoszeniach (brak ogłoszenia o zamówieniu w kopii BZP).",
         f"- Z {len(kpo)} projektów KPO (bez numeru BZP): {sum(1 for p in kpo if best[p[0]] and best[p[0]][3])} z kandydatem dopasowania (NIP + nazwa), zawsze do decyzji eksperta.",
         f"- Odczytane: **ogłoszenia {sum(notices.values())}**, **umowy {umowy[0]}**, **zdarzenia ryzyka {sum(ev.values())}** – każde ze źródłem (ogłoszenie + pole + cytat).",
         f"- Grupa porównawcza: **{nb} zakończonych umów** z BZP zbudowana w {T['bench']:.0f} s.",
         f"- Dokumenty: **{len([d for d in dres if 'blad' not in d])} z {len(dres)}** przetworzonych w {T['docs']:.0f} s (w tym skan przez OCR), {frag} fragmentów R01–R15, "
         f"weryfikacja z BZP: {dict(ver)}.",
         f"- Nowa inwestycja (przykład): {T['invest']:.1f} s · rekord testowy: {T['record']:.1f} s.", "",
         "## 2. Łączenie projektów z BZP", "", "| Status | Liczba | Udział |", "|---|---|---|"]
    for k in ("poprawne", "zatwierdzone", "niepewne", "brak"):
        if st.get(k):
            L.append(f"| {k} | {st[k]} | {pct(st[k], len(projs))} |")
    L += ["", "| Metoda | Liczba |", "|---|---|"] + [f"| {k} | {v} |" for k, v in met.most_common()]
    L += ["", f"Kontrola NIP zgodna w {nip_ok} najlepszych powiązaniach. Zasada: pewność 100% tylko przy zgodnym numerze ogłoszenia **i** NIP; "
          "wszystko inne trafia do kolejki eksperta (brak w kopii BZP ≠ brak publikacji).", "",
          "| Lp. | Odbiorca | Identyfikator | Status | Metoda | Podstawa |", "|---|---|---|---|---|---|"]
    for i, (pid, nazwa, benef, rodz, idp) in enumerate(projs, 1):
        r = best[pid]
        why = ("numer ogłoszenia + NIP zgodne" if r and r[0] == "poprawne" else
               f"podobieństwo nazwy {round((r[2] or 0) * 100)}% + NIP zgodny → do decyzji eksperta" if r and "rozmyte" in (r[1] or "") else
               "odtworzone z odwołań w późniejszych ogłoszeniach → do decyzji eksperta" if r and "odwołania" in (r[1] or "") else "—")
        L.append(f"| {i} | {benef} | {idp or '—'} | {r[0] if r else 'brak'} | {r[1] if r else '—'} | {why} |")
    L += ["", "## 3. Dane z ogłoszeń", "", "| Typ ogłoszenia | Liczba |", "|---|---|"] + \
         [f"| {ing.TYPE_PL.get(k, k)} | {v} |" for k, v in notices.most_common()] + \
         ["", f"Umowy: {umowy[0]} · wartość {umowy[1] or 0:,.0f} zł · wypłacono {umowy[2] or 0:,.0f} zł · po terminie: {late} ({pct(late, umowy[0])}).".replace(",", " "),
          f"Jakość danych: postępowania ze zduplikowanym ogłoszeniem o wykonaniu umowy: {dup} (liczone raz); luki odtworzone z odwołań: {gaps}.", "",
          "| Kod | Zdarzenia ryzyka |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(ev.items())]
    L += ["", "## 4. Grupa porównawcza (zakończone umowy z BZP)", "",
          f"| Pole | Pokrycie |", "|---|---|",
          f"| wartość umowy (4.4) | {pct(cov[1], cov[0])} |", f"| wypłacono (5.5) | {pct(cov[2], cov[0])} |",
          f"| termin planowany (4.2, data lub okres) | {pct(cov[3], cov[0])} |", f"| termin wykonania (5.2) | {pct(cov[4], cov[0])} |",
          f"| w terminie (5.3) | {pct(cov[5], cov[0])} |", f"| zmiana kosztu (wiarygodna, ±200%) | {pct(cov[6], cov[0])} |", "",
          "Podsektory (klasyfikacja MFiPR z przedmiotu zamówienia): " + ", ".join(f"{k} {v}" for k, v in subs.most_common()) + ".", "",
          "## 5. Dokumenty", "", "| Dokument | Format | Powiązanie | Fragmenty | Weryfikacja z BZP |", "|---|---|---|---|---|"]
    errs_ = [d for d in dres if "blad" in d]
    dres = docs.listing(db) + errs_
    for d in dres:
        if "blad" in d:
            L.append(f"| {d.get('nazwa')} | — | błąd: {d['blad']} | — | — |")
            continue
        vv = Counter(x["wynik"] for x in d.get("weryfikacja", []))
        pw = d.get("powiazanie") or {}
        L.append(f"| {d.get('nazwa')} | {d.get('format')} | {d.get('projekt') or '—'} ({pw.get('metoda')}, {round((pw.get('pewnosc') or 0) * 100)}%) | "
                 f"{len(d.get('fragmenty', []))} | {', '.join(f'{k}: {v}' for k, v in vv.items()) or '—'} |")
    if inv:
        P_ = inv["podsumowanie"]
        L += ["", "## 6. Analiza nowej inwestycji (przykład)", "",
              f"- Plik: {inv['plik']} ({inv['format']}, {inv['zdan']} zdań) · czas {T['invest']:.1f} s",
              f"- Gotowość opisu wg checklisty eksperckiej: **{P_['gotowosc_pct']}%** · ocena ryzyka: **{P_['ocena_ryzyka']}**",
              f"- Fakty z cytatem: {len(inv['fakty'])} · sygnały ryzyka z cytatem: {len([x for x in inv['ryzyka_z_opisu'] if x['pewnosc'] != 'informacja'])} · "
              f"sygnały z danych BZP: {len(inv['ryzyka_z_danych_bzp'])} · brakujące informacje: {len(inv['uzupelnienia'])}",
              f"- Grupa porównawcza: {inv['porownanie_bzp'].get('statystyka', {}).get('n_umow', '—')} umów ({inv['porownanie_bzp'].get('statystyka', {}).get('zakres', '')})",
              "- Checklista: " + "; ".join(f"{c_['id']}. {c_['status']}" for c_ in inv["checklista"])]
    if rec:
        W = rec["wskazniki"]
        L += ["", "## 7. Rekord testowy (przykład)", "",
              f"- {rec['rekord']['beneficjent']} · {rec['rekord']['id_publiczny']} → {W['status_powiazania']} · ocena: {W['ocena']} · "
              f"zmiana kosztu {W['zmiana_kosztu_pct']}% · zdarzenia: {len(rec['zdarzenia_ryzyka'])} · ogłoszenia: {len(rec['ogloszenia'])} · czas {T['record']:.1f} s"]
    L += ["", "## 8. Powtarzalność i ograniczenia", "",
          "- Wynik deterministyczny: te same dane wejściowe → te same liczby (reguły + statystyka). Model językowy, jeśli włączony, tylko dokłada propozycje z cytatem sprawdzonym w dokumencie.",
          "- Kopia BZP organizatora: brak ogłoszenia w kopii nie oznacza braku publikacji – takie przypadki są oznaczone i trafiają do eksperta.",
          "- Projekty KPO nie mają numeru BZP – dopasowanie jest zawsze propozycją do zatwierdzenia.",
          "- Brak zbioru referencyjnego od organizatora: „poprawne” oznacza podwójne potwierdzenie (numer + NIP), a nie ocenę z zewnętrznego wzorca.", ""]
    a.out.write_text("\n".join(L), encoding="utf-8")
    (a.out.with_suffix(".json")).write_text(json.dumps({"czasy_s": {k: round(v, 1) for k, v in T.items()}, "statusy": st, "metody": met,
                                                       "ogloszenia": notices, "zdarzenia": ev, "grupa": nb}, ensure_ascii=False, indent=1, default=dict), encoding="utf-8")
    print(f"Gotowe: {a.out}")


if __name__ == "__main__":
    main()
