#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS uzytkownicy(login TEXT PRIMARY KEY, nazwa TEXT, rola TEXT, haslo TEXT, sol TEXT,
  aktywny INT DEFAULT 1, utworzono TEXT, ostatnie_logowanie TEXT);
CREATE TABLE IF NOT EXISTS sesje(token TEXT PRIMARY KEY, login TEXT, utworzono TEXT, wygasa TEXT, ip TEXT);
CREATE TABLE IF NOT EXISTS logowania(czas TEXT, login TEXT, wynik TEXT, ip TEXT);
"""
ROLES = {"zatwierdzajacy": "ekspert zatwierdzający", "analityk": "ekspert analityk"}
RANK = {"analityk": 1, "zatwierdzajacy": 2}
SESSION_H = float(os.environ.get("RM_SESSION_H", 12))
COOKIE = "bidradar_sesja"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def setup(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def add_user(c: sqlite3.Connection, login: str, password: str, rola: str = "zatwierdzajacy", nazwa: str | None = None) -> None:
    if rola not in ROLES:
        raise ValueError(f"Nieznana rola „{rola}”. Dostępne: {', '.join(ROLES)}.")
    if len(password) < 8:
        raise ValueError("Hasło musi mieć co najmniej 8 znaków.")
    login = login.strip().lower()
    salt = secrets.token_bytes(16)
    c.execute("INSERT OR REPLACE INTO uzytkownicy(login,nazwa,rola,haslo,sol,aktywny,utworzono) VALUES(?,?,?,?,?,1,?)",
              (login, nazwa or login, rola, _hash(password, salt), salt.hex(), now()))
    c.execute("DELETE FROM sesje WHERE login=?", (login,))
    c.commit()


def check_password(c: sqlite3.Connection, login: str, password: str, ip: str = "") -> dict | None:
    login = (login or "").strip().lower()
    row = c.execute("SELECT login,nazwa,rola,haslo,sol,aktywny FROM uzytkownicy WHERE login=?", (login,)).fetchone()
    salt = bytes.fromhex(row[4]) if row else b"\0" * 16
    ok = hmac.compare_digest(_hash(password or "", salt), row[3] if row else "0" * 64) and bool(row and row[5])
    c.execute("INSERT INTO logowania VALUES(?,?,?,?)", (now(), login, "ok" if ok else "błąd", ip))
    if ok:
        c.execute("UPDATE uzytkownicy SET ostatnie_logowanie=? WHERE login=?", (now(), login))
    c.commit()
    return {"login": row[0], "nazwa": row[1], "rola": row[2]} if ok else None


def too_many_failures(c: sqlite3.Connection, login: str, ip: str, minutes: int = 10, limit: int = 8) -> bool:
    since = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    n = c.execute("SELECT COUNT(*) FROM logowania WHERE wynik='błąd' AND czas>=? AND (login=? OR ip=?)",
                  (since, (login or "").strip().lower(), ip)).fetchone()[0]
    return n >= limit


def new_session(c: sqlite3.Connection, login: str, ip: str = "") -> str:
    token = secrets.token_urlsafe(32)
    exp = (datetime.now() + timedelta(hours=SESSION_H)).isoformat(timespec="seconds")
    c.execute("DELETE FROM sesje WHERE wygasa<?", (now(),))
    c.execute("INSERT INTO sesje VALUES(?,?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), login, now(), exp, ip))
    c.commit()
    return token


def session_user(c: sqlite3.Connection, token: str | None) -> dict | None:
    if not token:
        return None
    row = c.execute("SELECT u.login,u.nazwa,u.rola FROM sesje s JOIN uzytkownicy u ON u.login=s.login "
                    "WHERE s.token=? AND s.wygasa>=? AND u.aktywny=1", (hashlib.sha256(token.encode()).hexdigest(), now())).fetchone()
    return {"login": row[0], "nazwa": row[1], "rola": row[2], "rola_opis": ROLES.get(row[2], row[2])} if row else None


def end_session(c: sqlite3.Connection, token: str | None) -> None:
    if token:
        c.execute("DELETE FROM sesje WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))
        c.commit()


def allowed(user: dict | None, need: str | None) -> bool:
    if need is None:
        return True
    return bool(user) and RANK.get(user["rola"], 0) >= RANK[need]


def count_users(c: sqlite3.Connection) -> int:
    return c.execute("SELECT COUNT(*) FROM uzytkownicy WHERE aktywny=1").fetchone()[0]


def bootstrap(c: sqlite3.Connection, folder: Path) -> list[tuple[str, str, str]]:
    if count_users(c):
        return []
    pw = os.environ.get("RM_DEMO_PASSWORD") or secrets.token_urlsafe(9)
    made = [("ekspert", "zatwierdzajacy", "Ekspert zatwierdzający"), ("analityk", "analityk", "Ekspert analityk")]
    for login, rola, nazwa in made:
        add_user(c, login, pw, rola, nazwa)
    if not os.environ.get("RM_DEMO_PASSWORD"):
        f = folder / "hasla_startowe.txt"
        f.write_text("BidRadar – konta startowe (plik jest w .gitignore, nie wysyłaj go do repozytorium)\n\n"
                     + "".join(f"login: {l:<10} hasło: {pw}   rola: {ROLES[r]}\n" for l, r, _ in made)
                     + "\nZmiana hasła: python3 rm_auth.py haslo ekspert\n", encoding="utf-8")
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
    return [(l, pw, r) for l, r, _ in made]


def main() -> None:
    ap = argparse.ArgumentParser(description="BidRadar – konta ekspertów")
    ap.add_argument("akcja", choices=["dodaj", "lista", "haslo", "usun"])
    ap.add_argument("login", nargs="?")
    ap.add_argument("--rola", default="zatwierdzajacy", choices=list(ROLES))
    ap.add_argument("--nazwa", help="imię i nazwisko widoczne w historii decyzji")
    ap.add_argument("--db", default=os.environ.get("RM_DB", "bidradar.db"))
    a = ap.parse_args()
    c = sqlite3.connect(a.db)
    setup(c)
    if a.akcja == "lista":
        rows = c.execute("SELECT login,nazwa,rola,aktywny,ostatnie_logowanie FROM uzytkownicy ORDER BY login").fetchall()
        for l, n, r, act, last in rows:
            print(f"{l:<20} {n or '':<28} {ROLES.get(r, r):<24} {'aktywne' if act else 'wyłączone':<10} ostatnio: {last or '—'}")
        print(f"\nKont: {len(rows)}")
        return
    if not a.login:
        sys.exit("Podaj login, np.: python3 rm_auth.py dodaj jan.kowalski")
    if a.akcja == "usun":
        c.execute("UPDATE uzytkownicy SET aktywny=0 WHERE login=?", (a.login.lower(),))
        c.execute("DELETE FROM sesje WHERE login=?", (a.login.lower(),))
        c.commit()
        print(f"Konto {a.login} wyłączone (historia decyzji zostaje).")
        return
    pw = os.environ.get("RM_NEW_PASSWORD") or getpass.getpass("Nowe hasło (min. 8 znaków): ")
    if not os.environ.get("RM_NEW_PASSWORD") and getpass.getpass("Powtórz hasło: ") != pw:
        sys.exit("Hasła się różnią – spróbuj jeszcze raz.")
    if a.akcja == "haslo":
        row = c.execute("SELECT rola,nazwa FROM uzytkownicy WHERE login=?", (a.login.lower(),)).fetchone()
        if not row:
            sys.exit(f"Nie ma konta {a.login}. Załóż je: python3 rm_auth.py dodaj {a.login}")
        add_user(c, a.login, pw, row[0], row[1])
    else:
        add_user(c, a.login, pw, a.rola, a.nazwa)
    print(f"Gotowe: {a.login} ({ROLES[a.rola if a.akcja == 'dodaj' else row[0]]}).")


if __name__ == "__main__":
    main()
