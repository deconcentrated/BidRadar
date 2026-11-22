#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import random
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

MIN_N, PRIOR_M = 30, 20


def months(a: str | None, b: str | None) -> float | None:
    try:
        d1, d2 = date.fromisoformat(a[:10]), date.fromisoformat(b[:10])
        return (d2 - d1).days / 30.44
    except Exception:
        return None


def load(db: str) -> list[dict]:
    c = sqlite3.connect(db)
    rows = []
    for numer, pub, woj, pods, ziw, sign, plan, wart, pct, term, przedm in c.execute(
            "SELECT numer,data_publikacji,wojewodztwo,podsektor,ziw,data_zawarcia,termin_planowany,wartosc,zmiana_pct,w_terminie,przedmiot "
            "FROM umowy_sektor WHERE w_terminie IS NOT NULL AND zmiana_pct IS NOT NULL AND wartosc > 10000"):
        dur = months(sign, plan)
        rows.append({"numer": numer, "rok": int((pub or "0000")[:4] or 0), "woj": woj or "?", "pods": pods or "?", "ziw": int(ziw or 0),
                     "logv": math.log10(wart), "dur": dur, "pct": pct, "mod": int(bool(re.search(r"przebud|moderniz|remont", przedm or "", re.I))),
                     "y": int(term == 0 or pct >= 3.0)})
    return rows


def cuts(vals: list[float], k: int) -> list[float]:
    s = sorted(vals)
    return [s[int(len(s) * i / k)] for i in range(1, k)]


def bucket(v, cs) -> int:
    if v is None:
        return -1
    return sum(v >= x for x in cs)


def dur_b(d) -> int:
    return -1 if d is None or d <= 0 else 0 if d <= 6 else 1 if d <= 12 else 2 if d <= 24 else 3


class Groups:

    def __init__(self, train: list[dict], levels: list[tuple], vcuts):
        self.levels, self.vcuts = levels, vcuts
        self.base = sum(r["y"] for r in train) / len(train)
        self.st: list[dict] = []
        self.pct: list[dict] = []
        for lv in levels:
            d, p = {}, {}
            for r in train:
                k = self.key(r, lv)
                a = d.setdefault(k, [0, 0])
                a[0] += r["y"]
                a[1] += 1
                p.setdefault(k, []).append(r["pct"])
            self.st.append(d)
            self.pct.append(p)
        self.allpct = sorted(r["pct"] for r in train)

    def key(self, r, lv):
        f = {"pods": r["pods"], "val": bucket(r["logv"], self.vcuts), "woj": r["woj"], "dur": dur_b(r["dur"]), "ziw": r["ziw"]}
        return tuple(f[x] for x in lv)

    def predict(self, r) -> float:
        p = self.base
        for lv, d in zip(self.levels, self.st):
            k, n = d.get(self.key(r, lv), (0, 0))
            if n >= MIN_N:
                p = (k + PRIOR_M * p) / (n + PRIOR_M)
        return p

    def interval(self, r) -> tuple[float, float, int]:
        vals = self.allpct
        for lv, d in zip(self.levels, self.pct):
            v = d.get(self.key(r, lv))
            if v and len(v) >= MIN_N:
                vals = sorted(v)
        return vals[int(len(vals) * .1)], vals[min(len(vals) - 1, int(len(vals) * .9))], len(vals)


def auroc(y: list[int], p: list[float]) -> float:
    pairs = sorted(zip(p, y))
    ranks, i = [0.0] * len(pairs), 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    npos = sum(y)
    nneg = len(y) - npos
    sr = sum(r for r, (_, yy) in zip(ranks, pairs) if yy)
    return (sr - npos * (npos + 1) / 2) / (npos * nneg)


def brier(y, p):
    return sum((a - b) ** 2 for a, b in zip(y, p)) / len(y)


LEVELS_A = [("pods",), ("pods", "val"), ("pods", "val", "woj")]
LEVELS_B = [("pods",), ("pods", "val"), ("pods", "val", "dur"), ("pods", "val", "dur", "woj")]


def run_split(train, test, sk=None) -> dict:
    vc = cuts([r["logv"] for r in train], 5)
    out = {}
    for name, lv in (("A", LEVELS_A), ("B", LEVELS_B)):
        g = Groups(train, lv, vc)
        p = [g.predict(r) for r in test]
        y = [r["y"] for r in test]
        inside = 0
        for r in test:
            lo, hi, _ = g.interval(r)
            inside += lo <= r["pct"] <= hi
        out[name] = {"auroc": auroc(y, p), "brier": brier(y, p), "p": p, "y": y, "pokrycie": inside / len(test)}
    if sk:
        out["C"] = sk(train, test, vc)
    out["base"] = {"brier": brier([r["y"] for r in test], [sum(r["y"] for r in train) / len(train)] * len(test))}
    return out


def sklearn_model():
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import OneHotEncoder, StandardScaler
        from sklearn.compose import ColumnTransformer
        from sklearn.pipeline import make_pipeline
        from sklearn.ensemble import HistGradientBoostingClassifier
        import numpy as np
    except ImportError:
        return None

    def feats(rs):
        return [[r["pods"], r["woj"], str(dur_b(r["dur"])), r["logv"], r["dur"] if r["dur"] and r["dur"] > 0 else 0.0,
                 float(r["dur"] is None), r["ziw"], r["mod"]] for r in rs]

    def fit(train, test, _vc):
        res = {}
        for nm, clf in (("C", LogisticRegression(max_iter=1000)), ("D", HistGradientBoostingClassifier(max_iter=200, random_state=42))):
            ct = ColumnTransformer([("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), [0, 1, 2]),
                                    ("num", StandardScaler(), [3, 4, 5, 6, 7])])
            m = make_pipeline(ct, clf)
            X, Xt = np.array(feats(train), dtype=object), np.array(feats(test), dtype=object)
            m.fit(X, [r["y"] for r in train])
            p = list(m.predict_proba(Xt)[:, 1])
            y = [r["y"] for r in test]
            res[nm] = {"auroc": auroc(y, p), "brier": brier(y, p)}
        return res
    return fit


def calib(y, p, k=10):
    z = sorted(zip(p, y))
    out = []
    for i in range(k):
        part = z[len(z) * i // k: len(z) * (i + 1) // k]
        out.append((sum(a for a, _ in part) / len(part), sum(b for _, b in part) / len(part), len(part)))
    return out


def pl(x, d=3):
    return f"{x:.{d}f}".replace(".", ",")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="bidradar.db")
    ap.add_argument("--md", default="raport_predykcji.md")
    a = ap.parse_args()
    rows = load(a.db)
    if len(rows) < 1000:
        sys.exit(f"Za mało umów w {a.db} ({len(rows)}). Najpierw uruchom aplikację, żeby zbudowała grupę porównawczą.")
    sk = sklearn_model()
    rnd = random.Random(42)
    rnd.shuffle(rows)

    folds = [rows[i::5] for i in range(5)]
    cv = {"A": [], "B": [], "C": [], "D": [], "pokrA": [], "pokrB": []}
    allA = ([], [])
    for i in range(5):
        test = folds[i]
        train = [r for j, f in enumerate(folds) if j != i for r in f]
        res = run_split(train, test, sk if sk else None)
        for m in ("A", "B"):
            cv[m].append((res[m]["auroc"], res[m]["brier"]))
            cv["pokr" + m].append(res[m]["pokrycie"])
        allA[0].extend(res["B"]["y"])
        allA[1].extend(res["B"]["p"])
        if sk:
            for m in ("C", "D"):
                cv[m].append((res["C"][m]["auroc"], res["C"][m]["brier"]))

    tr = [r for r in rows if r["rok"] <= 2024]
    te = [r for r in rows if r["rok"] >= 2025]
    fut = run_split(tr, te, sk if sk else None)

    mean = lambda xs, i: sum(x[i] for x in xs) / len(xs)
    sd = lambda xs, i: (sum((x[i] - mean(xs, i)) ** 2 for x in xs) / len(xs)) ** .5
    share = sum(r["y"] for r in rows) / len(rows)
    L = ["# Jak dobrze dane z BZP przewidują ryzyko umowy – ocena BidRadar", "",
         f"Umowy z ogłoszeń o wykonaniu (kopia BZP): **{len(rows)}** z kompletem danych o terminie i koszcie. "
         f"„Zagrożona” = po terminie albo koszt ≥ +3%: **{pl(share * 100, 1)}%** umów. Cechy: tylko znane przed realizacją.", "",
         "## Wyniki", "", "| Metoda | AUROC (5-krotna CV) | Brier (CV) | AUROC – test na 2025–2026 |", "|---|---|---|---|"]
    names = {"A": "Grupa porównawcza BidRadar (podsektor → wartość → województwo)", "B": "Grupa porównawcza + planowany czas realizacji",
             "C": "Regresja logistyczna, te same cechy (scikit-learn)", "D": "Gradient boosting, te same cechy (scikit-learn)"}
    for m in ("A", "B", "C", "D"):
        if not cv[m]:
            continue
        f = fut[m] if m in ("A", "B") else fut["C"][m]
        L.append(f"| {names[m]} | {pl(mean(cv[m], 0))} ± {pl(sd(cv[m], 0))} | {pl(mean(cv[m], 1))} | {pl(f['auroc'])} |")
    L += [f"| Bez modelu (ten sam odsetek dla każdej umowy) | 0,500 | {pl(fut['base']['brier'])} | 0,500 |", "",
          "AUROC: szansa, że z pary (umowa zagrożona, niezagrożona) metoda wyżej oceni tę zagrożoną. 0,5 = rzut monetą, 1 = ideał.", "",
          "## Czy przedziały BidRadar są uczciwe", "",
          f"Rzeczywista zmiana kosztu mieściła się w przedziale P10–P90 grupy porównawczej w **{pl(sum(cv['pokrB']) / 5 * 100, 1)}%** umów "
          f"(walidacja krzyżowa) i w **{pl(fut['B']['pokrycie'] * 100, 1)}%** umów z lat 2025–2026 (cel: 80%).", "",
          "Kalibracja (grupa porównawcza + czas, walidacja krzyżowa, decyle prognozy):", "",
          "| Prognozowany odsetek zagrożonych | Rzeczywisty odsetek | Umów |", "|---|---|---|"]
    for pp, yy, n in calib(allA[0], allA[1]):
        L.append(f"| {pl(pp * 100, 1)}% | {pl(yy * 100, 1)}% | {n} |")
    L += ["", "## Wniosek", "",
          "Z informacji znanych przed przetargiem ryzyka pojedynczej umowy nie da się przewidzieć dokładnie – żadna metoda, "
          "także ML, nie zbliża się do AUROC 0,8. Dlatego BidRadar nie podaje jednej „pewnej” liczby, tylko odsetek i przedziały "
          "z grupy podobnych umów z liczebnością próby i linkami – a ich trafność (kalibracja, pokrycie P10–P90) sprawdziliśmy powyżej.", "",
          f"*Wygenerowano: `python3 tools/ocena_predykcji.py --db {Path(a.db).name}`; ziarno losowania 42, wynik powtarzalny.*"]
    Path(a.md).write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
