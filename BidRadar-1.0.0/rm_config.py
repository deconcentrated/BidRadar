from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIR = HERE / "konfiguracja"
SCHEMA = "CREATE TABLE IF NOT EXISTS ustawienia(k TEXT PRIMARY KEY, v TEXT, kto TEXT, czas TEXT);"

DEFAULTS = {
    "wagi": {"tekst": 0.5, "wartosc": 0.25, "region": 0.15, "formula": 0.1},
    "podsektory": {"sektor": "Gospodarka wodno-kanalizacyjna", "domyslny": "Inne obiekty wod-kan", "reguly": []},
    "zrodla": {"bzp": {"url": "http://127.0.0.1:8010"}, "ted": {"wlaczone": False}, "monitoring": {"co_ile_godzin": 6}},
}


def _clean(d):
    return {k: v for k, v in d.items() if not k.startswith("_")} if isinstance(d, dict) else d


@lru_cache(maxsize=None)
def file(name: str) -> dict:
    f = DIR / f"{name}.json"
    try:
        return _clean(json.loads(f.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return DEFAULTS.get(name, {})


def get(c: sqlite3.Connection | None, name: str):
    if c is not None:
        try:
            c.execute(SCHEMA)
            row = c.execute("SELECT v FROM ustawienia WHERE k=?", (name,)).fetchone()
            if row:
                return json.loads(row[0])
        except sqlite3.Error:
            pass
    return file(name)


def put(c: sqlite3.Connection, name: str, value, who: str) -> None:
    c.execute(SCHEMA)
    c.execute("INSERT OR REPLACE INTO ustawienia VALUES(?,?,?,?)",
              (name, json.dumps(value, ensure_ascii=False), who, datetime.now().isoformat(timespec="seconds")))
    c.commit()


def meta(c: sqlite3.Connection, name: str) -> dict | None:
    c.execute(SCHEMA)
    row = c.execute("SELECT kto, czas FROM ustawienia WHERE k=?", (name,)).fetchone()
    return {"kto": row[0], "czas": row[1]} if row else None


def weights(c: sqlite3.Connection | None = None) -> dict:
    w = {**DEFAULTS["wagi"], **{k: v for k, v in (get(c, "wagi") or {}).items() if k in DEFAULTS["wagi"]}}
    w = {k: max(0.0, float(v)) for k, v in w.items()}
    s = sum(w.values()) or 1.0
    return {k: v / s for k, v in w.items()}


def validate_weights(v: dict) -> dict:
    out = {}
    for k in DEFAULTS["wagi"]:
        try:
            x = float(v.get(k, DEFAULTS["wagi"][k]))
        except (TypeError, ValueError):
            raise ValueError(f"Waga „{k}” musi być liczbą.")
        if not 0 <= x <= 10:
            raise ValueError(f"Waga „{k}” musi być w przedziale 0–10.")
        out[k] = x
    if not sum(out.values()):
        raise ValueError("Przynajmniej jedna waga musi być większa od zera.")
    return out


@lru_cache(maxsize=1)
def _rules() -> tuple[str, list[tuple[str, list[re.Pattern]]]]:
    cfg = file("podsektory")
    rules = [(r["nazwa"], [re.compile(p) for p in r.get("wzorce", [])]) for r in cfg.get("reguly", [])]
    return cfg.get("domyslny", "Inne"), rules


def reload() -> None:
    file.cache_clear()
    _rules.cache_clear()


def subsector(name: str) -> str | None:
    default, rules = _rules()
    if not rules:
        return None
    n = (name or "").lower()
    for label, pats in rules:
        if any(p.search(n) for p in pats):
            return label
    return default


def rules_hash() -> str:
    return hashlib.sha1(json.dumps(file("podsektory"), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]
