#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("RM_LLM_MASKA", "0")

import rm_ingest as ing
import ocena_predykcji as op


def facts(notices: list[dict]) -> dict | None:
    perf = [n for n in notices if n["noticeType"] == "ContractPerformingNotice"]
    if not perf:
        return None
    late, wu, wy = False, 0.0, 0.0
    for p in perf:
        s = ing.text(p["htmlBody"])
        late |= ing.field(r"5\.3\.\)[^:]*:\s*(\S+)", s).lower().startswith("nie")
        wu += ing.money(ing.field(r"4\.4\.\) Wartość umowy[^:]*:\s*([\d\s,\.]+)", s)) or 0
        wy += ing.money(ing.field(r"5\.5\.\)[^:]*:\s*([\d\s,\.]+)", s)) or 0
    return {"w_terminie": "nie" if late else "tak", "zmiana_kosztu_pct": round((wy - wu) / wu * 100, 1) if wu and wy else None}


def facts_ok(got: dict | None, truth: dict) -> bool:
    if not got or got.get("w_terminie") != truth["w_terminie"]:
        return False
    if truth["zmiana_kosztu_pct"] is None:
        return True
    try:
        return abs(float(got.get("zmiana_kosztu_pct")) - truth["zmiana_kosztu_pct"]) <= 2.0
    except (TypeError, ValueError):
        return False


PROMPT_A = """Jesteś ekspertem od zamówień publicznych w Polsce. Na podstawie rekordu projektu wskaż ogłoszenie
o zamówieniu w Biuletynie Zamówień Publicznych (BZP, e-Zamówienia) dotyczące tej inwestycji oraz przebieg umowy.
Rekord:
- nazwa przedsięwzięcia: {nazwa}
- zamawiający: {benef}, NIP {nip}
- województwo: {woj}, siedziba: {miasto}
Odpowiedz wyłącznie obiektem JSON:
{{"numer_ogloszenia_bzp": "RRRR/BZP NNNNNNNN/NN albo null", "w_terminie": "tak|nie|null", "zmiana_kosztu_pct": liczba albo null}}
gdzie zmiana_kosztu_pct = (kwota wypłacona − wartość umowy) / wartość umowy × 100."""

PROMPT_C = """Oceń ryzyko dla zamówień na roboty wodno-kanalizacyjne w Polsce. „Zagrożona” = umowa zakończona po terminie
ALBO wypłacono co najmniej 3% więcej niż wartość umowy. Dla każdej umowy podaj prawdopodobieństwo (0–1), że będzie zagrożona.
{lista}
Odpowiedz wyłącznie obiektem JSON: {{"p": [liczby w tej samej kolejności, {n} pozycji]}}"""


def ask(prompt: str, pause: float) -> str:
    import rm_llm as llm
    out = llm.chat("Odpowiadasz wyłącznie poprawnym obiektem JSON.", prompt, max_tokens=1500, timeout=120)
    time.sleep(pause)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "bidradar.db"))
    ap.add_argument("--xlsx", default=os.environ.get("RM_XLSX") or str(ROOT / "paczka/Risk_Miner_dane_projekty.xlsx"))
    ap.add_argument("--url", default=os.environ.get("RM_BZP_URL", "http://127.0.0.1:8010"))
    ap.add_argument("--tylko-nasze", action="store_true")
    ap.add_argument("--n", type=int, default=200, help="liczba umów w zadaniu C")
    ap.add_argument("--pauza", type=float, default=float(os.environ.get("LLM_PAUZA", 4)), help="sekundy między zapytaniami (limity darmowych planów)")
    a = ap.parse_args()

    api = ing.Bzp(a.url, lambda *x: None)
    if not api.check():
        sys.exit(f"Kopia BZP nie odpowiada pod {a.url} – uruchom ją (bash uruchom.sh robi to sam).")
    use_llm = not a.tylko_nasze
    model = None
    if use_llm:
        import rm_llm as llm
        st = llm.status()
        if st["tryb"] != "model + reguły":
            sys.exit("Model wyłączony w .env – ustaw LLM_PROVIDER/LLM_MODEL albo uruchom z --tylko-nasze.")
        model = st["model"]
        print(f"Model: {model} ({st['gdzie']})")

    rows = []
    for r in ing.read_xlsx(Path(a.xlsx)):
        num = ing.bzp_base(r.get("Identyfikator publiczny") or "")
        if not num:
            continue
        truth_links = ing.link_project(api, r)
        t = truth_links[0] if truth_links and truth_links[0]["ocid"] else None
        if not t:
            continue
        tf = facts(t["ogloszenia"])
        blind = ing.link_project(api, {**r, "Identyfikator publiczny": ""})
        b = blind[0] if blind and blind[0]["ocid"] else None
        ours_a = bool(b and b["ocid"] == t["ocid"])
        ours_b = bool(tf and b and facts_ok(facts(b["ogloszenia"]), tf))
        row = {"projekt": r["Identyfikator rekordu"], "odbiorca": r["Nazwa odbiorcy"], "numer": num, "ocid": t["ocid"], "prawda_fakty": tf,
               "bidradar": {"ocid": b["ocid"] if b else None, "pewnosc": b["pewnosc"] if b else 0, "A": ours_a, "B": ours_b}}
        if use_llm:
            try:
                raw = ask(PROMPT_A.format(nazwa=r["Nazwa przedsięwzięcia"], benef=r["Nazwa odbiorcy"], nip=ing.digits(r["Identyfikator odbiorcy"]),
                                          woj=r["Województwo"], miasto=r["Siedziba odbiorcy"]), a.pauza)
                import rm_llm as llm
                ans = llm.parse_json(raw)
            except Exception as e:
                ans = {"blad": str(e)[:200]}
            got_num = ing.bzp_base(str(ans.get("numer_ogloszenia_bzp") or ""))
            row["model"] = {"odpowiedz": ans, "A": got_num == num, "B": bool(tf and facts_ok(ans, tf)), "bez_odpowiedzi": not got_num}
        rows.append(row)
        print(f"  {row['projekt']} {r['Nazwa odbiorcy'][:28]:<28} BidRadar A={'tak' if ours_a else 'nie'} B={'tak' if ours_b else 'nie'}"
              + (f" · model A={'tak' if row['model']['A'] else 'nie'} B={'tak' if row['model']['B'] else 'nie'}" if use_llm else ""))
    nB = [x for x in rows if x["prawda_fakty"]]

    data = op.load(a.db)
    if len(data) < 1000:
        sys.exit(f"Za mało umów w {a.db} – uruchom aplikację, żeby zbudowała grupę porównawczą.")
    train = [r for r in data if r["rok"] <= 2024]
    test = [r for r in data if r["rok"] >= 2025]
    random.Random(42).shuffle(test)
    test = test[:a.n]
    g = op.Groups(train, op.LEVELS_A, op.cuts([r["logv"] for r in train], 5))
    ours_p = [g.predict(r) for r in test]
    y = [r["y"] for r in test]
    c_res = {"n": len(test), "udzial_zagrozonych": round(sum(y) / len(y), 3), "bidradar_auroc": round(op.auroc(y, ours_p), 3)}
    if use_llm:
        con = sqlite3_rows(a.db, [r["numer"] for r in test])
        model_p = []
        for i in range(0, len(test), 20):
            part = test[i:i + 20]
            lista = "\n".join(f"{k + 1}. {con.get(r['numer'], '')[:110]} | podsektor: {r['pods']} | woj. {r['woj']} | wartość umowy: {10 ** r['logv'] / 1e6:.2f} mln zł brutto | "
                              f"planowany czas: {round(r['dur']) if r['dur'] and r['dur'] > 0 else 'b.d.'} mies. | zaprojektuj i wybuduj: {'tak' if r['ziw'] else 'nie'}"
                              for k, r in enumerate(part))
            try:
                import rm_llm as llm
                ps = llm.parse_json(ask(PROMPT_C.format(lista=lista, n=len(part)), a.pauza)).get("p", [])
                ps = [float(x) for x in ps][:len(part)]
            except Exception as e:
                print(f"  zadanie C, porcja {i // 20 + 1}: {str(e)[:120]}")
                ps = []
            model_p += ps + [0.5] * (len(part) - len(ps))
            print(f"  zadanie C: {min(i + 20, len(test))}/{len(test)}")
        c_res["model_auroc"] = round(op.auroc(y, model_p), 3)

    pct = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else None
    out = {"czas": datetime.now().isoformat(timespec="seconds"), "model": model,
           "A": {"n": len(rows), "bidradar_pct": pct([x["bidradar"]["A"] for x in rows]),
                 "model_pct": pct([x["model"]["A"] for x in rows]) if use_llm else None,
                 "model_bez_odpowiedzi_pct": pct([x["model"]["bez_odpowiedzi"] for x in rows]) if use_llm else None},
           "B": {"n": len(nB), "bidradar_pct": pct([x["bidradar"]["B"] for x in nB]), "model_pct": pct([x["model"]["B"] for x in nB]) if use_llm else None},
           "C": c_res, "szczegoly": rows}
    if not use_llm:
        try:
            old = json.loads((ROOT / "konfiguracja/porownanie.json").read_text(encoding="utf-8"))
            if old.get("model"):
                out["model"] = old["model"]
                out["czas_modelu"] = old.get("czas_modelu") or old.get("czas")
                for k in ("A", "B"):
                    out[k]["model_pct"] = old[k].get("model_pct")
                out["A"]["model_bez_odpowiedzi_pct"] = old["A"].get("model_bez_odpowiedzi_pct")
                out["C"]["model_auroc"] = old["C"].get("model_auroc")
        except Exception:
            pass
    else:
        out["czas_modelu"] = out["czas"]
    (ROOT / "konfiguracja/porownanie.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    L = ["# BidRadar a model językowy ogólnego przeznaczenia", "", f"*{out['czas']} · model: {out['model'] or 'nie mierzono'}*", "",
         "| Zadanie | BidRadar | Model językowy | n |", "|---|---|---|---|",
         f"| A. Wyszukanie postępowania w BZP (bez numeru w pytaniu) | {out['A']['bidradar_pct']}% | {out['A']['model_pct'] if out['A']['model_pct'] is not None else '—'}% | {out['A']['n']} |",
         f"| B. Fakty o realizacji: termin i zmiana kosztu ±2 pkt | {out['B']['bidradar_pct']}% | {out['B']['model_pct'] if out['B']['model_pct'] is not None else '—'}% | {out['B']['n']} |",
         f"| C. Ocena ryzyka nowych umów (AUROC, 50% = losowo) | {round(c_res['bidradar_auroc'] * 100, 1)}% | "
         f"{round(out['C']['model_auroc'] * 100, 1) if out['C'].get('model_auroc') is not None else '—'}% | {c_res['n']} |", "",
         "Prawda w A: numer ogłoszenia z arkusza organizatora. W B: ogłoszenie o wykonaniu umowy w BZP. W C: wynik umów z lat 2025–2026, "
         "których żadna metoda nie widziała. Model pytany bez dostępu do internetu i bazy.", ""]
    (ROOT / "raport_porownania.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


def sqlite3_rows(db: str, nums: list[str]) -> dict:
    import sqlite3
    c = sqlite3.connect(db)
    q = ",".join("?" * len(nums))
    return {n: p or "" for n, p in c.execute(f"SELECT numer, przedmiot FROM umowy_sektor WHERE numer IN ({q})", nums)}


if __name__ == "__main__":
    main()
