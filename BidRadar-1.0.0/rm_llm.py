#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_env():
    for f in (HERE / ".env", HERE / "config.env"):
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
                if m and not line.lstrip().startswith("#") and m.group(1) not in os.environ:
                    os.environ[m.group(1)] = m.group(2).strip('"\'')


load_env()


def config() -> dict:
    prov = (os.environ.get("LLM_PROVIDER") or "").lower().strip()
    key = os.environ.get("LLM_API_KEY", "")
    base = os.environ.get("LLM_BASE_URL", "")
    model = os.environ.get("LLM_MODEL", "")
    if not prov and os.environ.get("MISTRAL_API_KEY"):
        prov = "mistral"
    if prov == "mistral":
        key = key if key and key != "wklej_swoj_klucz" else os.environ.get("MISTRAL_API_KEY", "")
        base = base or "https://api.mistral.ai/v1"
        model = model or "mistral-small-latest"
    if not prov:
        prov = "openai" if (base and model and (key and key != "wklej_swoj_klucz" or "11434" in base)) else "none"
    if prov == "ollama" and not base:
        base = "http://127.0.0.1:11434/v1"
    if prov == "anthropic" and not base:
        base = "https://api.anthropic.com"
    enabled = prov in ("openai", "ollama", "anthropic", "mistral") and bool(model) and (prov == "ollama" or "11434" in base or (key and key != "wklej_swoj_klucz"))
    where = "lokalnie – dane nie opuszczają komputera" if prov == "ollama" or "127.0.0.1" in base or "localhost" in base else \
            ("UE" if "mistral" in base else "API zewnętrzne")
    return {"enabled": enabled, "provider": prov if enabled else "none", "model": model if enabled else "", "base": base, "key": key, "where": where if enabled else ""}


def status() -> dict:
    c = config()
    return {"tryb": "model + reguły" if c["enabled"] else "reguły + statystyka", "dostawca": c["provider"], "model": c["model"], "gdzie": c["where"],
            "maskowanie_danych_osobowych": bool(c["enabled"]) and must_mask(c)}


def ping() -> dict:
    c = config()
    if not c["enabled"]:
        return {"ok": False, "blad": "Model wyłączony. Ustaw LLM_PROVIDER i klucz w pliku .env (wzór: config.example.env).", **status()}
    t = time.time()
    try:
        out = chat("Odpowiadasz wyłącznie obiektem JSON.", 'Zwróć {"ok": true}.', max_tokens=200, timeout=90)
        return {"ok": True, "czas_ms": round((time.time() - t) * 1000), "odpowiedz": out[:80], **status()}
    except Exception as e:
        return {"ok": False, "blad": f"{e}"[:300], "czas_ms": round((time.time() - t) * 1000), **status()}


def _post(url: str, body: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
    host = urllib.parse.urlparse(url).hostname or ""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if host in ("127.0.0.1", "localhost") else urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as r:
        return json.load(r)


import urllib.error
import urllib.parse


PII = [("PESEL", re.compile(r"(?<!\d)\d{11}(?!\d)")),
       ("RACHUNEK", re.compile(r"(?<!\w)(?:PL)?\d{2}(?:[ ]?\d{4}){6}(?!\d)")),
       ("NIP", re.compile(r"(?<!\d)\d{3}[- ]?(?:\d{3}[- ]?\d{2}[- ]?\d{2}|\d{2}[- ]?\d{2}[- ]?\d{3})(?!\d)")),
       ("REGON", re.compile(r"(?<!\d)\d{9}(?:\d{5})?(?!\d)")),
       ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
       ("TELEFON", re.compile(r"(?<![\w\d])(?:\+48[ ]?)?(?:\d{3}[ -]\d{3}[ -]\d{3}|\d{2}[ -]\d{3}[ -]\d{2}[ -]\d{2})(?!\d)"))]


def must_mask(c: dict | None = None) -> bool:
    c = c or config()
    if os.environ.get("RM_LLM_MASKA", "").strip() in ("0", "nie"):
        return False
    return os.environ.get("RM_LLM_MASKA", "").strip() in ("1", "tak") or c.get("where") == "API zewnętrzne"


def mask(text: str) -> tuple[str, dict]:
    back: dict[str, str] = {}
    seen: dict[str, str] = {}
    for name, rx in PII:
        def rep(m, name=name):
            v = m.group(0)
            if v not in seen:
                seen[v] = f"[{name}_{sum(1 for k in back if k.startswith('[' + name)) + 1}]"
                back[seen[v]] = v
            return seen[v]
        text = rx.sub(rep, text)
    return text, back


def unmask(text: str, back: dict) -> str:
    for k, v in back.items():
        text = text.replace(k, v)
    return text


def chat(system: str, user: str, max_tokens: int = 2500, timeout: int = 90) -> str:
    c = config()
    if not c["enabled"]:
        raise RuntimeError("model wyłączony")
    if must_mask(c):
        user, back = mask(user)
        return unmask(_chat(c, system, user, max_tokens, timeout), back)
    return _chat(c, system, user, max_tokens, timeout)


def _chat(c: dict, system: str, user: str, max_tokens: int, timeout: int) -> str:
    if c["provider"] == "anthropic":
        r = _post(c["base"].rstrip("/") + "/v1/messages",
                  {"model": c["model"], "max_tokens": max_tokens, "temperature": 0, "system": system, "messages": [{"role": "user", "content": user}]},
                  {"x-api-key": c["key"], "anthropic-version": "2023-06-01"}, timeout)
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
    hdr = {"Authorization": f"Bearer {c['key']}"} if c["key"] and c["key"] != "wklej_swoj_klucz" else {}
    body = {"model": c["model"], "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if c["provider"] == "mistral":
        body["response_format"] = {"type": "json_object"}
        body["random_seed"] = 42
    if "generativelanguage.googleapis.com" in c["base"]:
        effort = (os.environ.get("LLM_MYSLENIE") or "low").strip()
        if effort not in ("", "0", "domyslne"):
            body["reasoning_effort"] = effort
        body["max_tokens"] = max(max_tokens, 4096)
        timeout = max(timeout, 90)
    timeout = int(os.environ.get("LLM_TIMEOUT") or timeout)
    backup = (os.environ.get("LLM_MODEL_ZAPASOWY") or "").strip()
    for attempt in range(4):
        try:
            r = _post(c["base"].rstrip("/") + "/chat/completions", body, hdr, timeout)
            out = r["choices"][0]["message"].get("content") or ""
            if not out.strip() and attempt < 3:
                body["max_tokens"] = body["max_tokens"] * 2
                continue
            return out
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 400 and "reasoning" in detail and "reasoning_effort" in body:
                body.pop("reasoning_effort")
                continue
            busy = e.code in (429, 500, 502, 503, 504)
            if busy and attempt == 2 and backup and body["model"] != backup:
                body["model"] = backup
                continue
            if busy and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            if e.code == 401:
                raise RuntimeError("klucz API odrzucony (401) – sprawdź MISTRAL_API_KEY / LLM_API_KEY w pliku .env") from None
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None
        except (TimeoutError, OSError) as e:
            if backup and body["model"] != backup:
                body["model"] = backup
                continue
            if attempt < 1:
                continue
            raise RuntimeError(f"model nie odpowiedział w {timeout} s ({type(e).__name__}) – ustaw LLM_MODEL_ZAPASOWY lub LLM_TIMEOUT w .env") from None
    raise RuntimeError("model przeciążony lub limit zapytań – spróbuj za chwilę albo ustaw LLM_MODEL_ZAPASOWY w .env")


def parse_json(t: str):
    t = t.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1)
    i, j = min([x for x in (t.find("{"), t.find("[")) if x >= 0] or [0]), max(t.rfind("}"), t.rfind("]"))
    return json.loads(t[i:j + 1])


def norm(s: str) -> str:
    s = (s or "").lower().replace("\xa0", " ")
    s = re.sub(r"[„”\"'`´«»]", "", s)
    s = re.sub(r"[‐-―−-]", "-", s)
    return re.sub(r"\s+", " ", s).strip(" .…")


def locate(quote: str, sents: list[tuple[str, str]]) -> str | None:
    q = norm(quote)
    if len(q) < 12:
        return None
    for loc, s in sents:
        if q in norm(s):
            return loc
    full = norm(" ".join(s for _, s in sents))
    if q in full:
        for loc, s in sents:
            if norm(s)[:40] and norm(s)[:40] in q or q[:40] in norm(s):
                return loc
        return "cały dokument"
    return None


CATALOG_TXT = """R01 Niepełny lub niespójny zakres; R02 Niedostateczne rozpoznanie stanu istniejącego; R03 Kolizje, dostęp do terenu i prawa do nieruchomości;
R04 Decyzje, pozwolenia i uzgodnienia zależne od podmiotów trzecich; R05 Niejasny lub nieadekwatny podział odpowiedzialności;
R06 Nierealistyczny harmonogram i nieuwzględnione zależności; R07 Nieprzewidziane zmiany i roboty dodatkowe; R08 Model wynagrodzenia niedopasowany do niepewności zakresu;
R09 Niejednoznaczne lub niemierzalne wymagania techniczne; R10 Niedookreślone próby, rozruch i odbiór; R11 Postępowanie podatne na odwołanie lub ograniczenie konkurencji;
R12 Niewystarczająca zdolność wykonawcy lub łańcucha dostaw; R13 Ryzyko finansowania, budżetu i płatności; R14 Nadmierne koszty eksploatacji i utrzymania;
R15 Nieosiągnięcie efektu technologicznego, użytkowego lub środowiskowego"""

SYSTEM = ("Jesteś asystentem eksperta MFiPR oceniającego przygotowanie inwestycji wodno-kanalizacyjnych. "
          "Pracujesz WYŁĄCZNIE na podanym tekście. Każdy wniosek musi mieć dosłowny cytat z tekstu (fragment 15–200 znaków, skopiowany bez zmian). "
          "Nie zgaduj: jeśli tekst czegoś nie mówi, nie twórz wniosku. Rozróżniaj: 'tekst nie wspomina' vs 'tekst potwierdza brak'. "
          "Odpowiadasz tylko poprawnym JSON, po polsku.")


def invest(sents: list[tuple[str, str]], rule_codes: list[str]) -> dict:
    text = "\n".join(f"[{loc}] {s}" for loc, s in sents)[:60000]
    user = (f"Katalog ryzyk:\n{CATALOG_TXT}\n\nReguły już wskazały kody: {', '.join(sorted(set(rule_codes))) or 'brak'}.\n"
            "Zadanie: znajdź w opisie inwestycji sygnały ryzyka (także inne niż już wskazane) oraz najważniejsze fakty.\n"
            'Zwróć JSON: {"ryzyka":[{"kod":"R0x","sygnal":"…","zdarzenie":"…","wplyw":"…","srodki":"…","cytat":"dosłowny fragment","pewnosc":"niska|średnia"}],'
            '"fakty":[{"cecha":"…","wartosc":"…","cytat":"dosłowny fragment"}],"streszczenie":"2–3 zdania dla eksperta"}\n'
            "Maksymalnie 10 ryzyk i 12 faktów.\n\nTEKST:\n" + text)
    t0 = time.time()
    raw = chat(SYSTEM, user)
    data = parse_json(raw)
    ok, bad = [], []
    for r in (data.get("ryzyka") or [])[:10]:
        kod = str(r.get("kod", "")).upper()[:3]
        loc = locate(r.get("cytat", ""), sents)
        if not re.fullmatch(r"R(0[1-9]|1[0-5])", kod) or not loc:
            bad.append({**r, "powod": "cytat nie występuje w dokumencie" if not loc else "kod spoza katalogu"})
            continue
        ok.append({"kod": kod, "sygnal": str(r.get("sygnal", ""))[:200], "zdarzenie": str(r.get("zdarzenie", ""))[:200],
                   "wplyw": str(r.get("wplyw", ""))[:200], "srodki": str(r.get("srodki", ""))[:240],
                   "pewnosc": "średnia" if r.get("pewnosc") == "średnia" else "niska",
                   "dowody": [{"miejsce": loc, "cytat": str(r.get("cytat"))[:300]}], "zrodlo": "model (cytat zweryfikowany w dokumencie)",
                   "status": "propozycja modelu – do decyzji eksperta"})
    facts = []
    for f in (data.get("fakty") or [])[:12]:
        loc = locate(f.get("cytat", ""), sents)
        if loc:
            facts.append({"cecha": str(f.get("cecha", ""))[:60], "wartosc": str(f.get("wartosc", ""))[:160], "miejsce": loc, "cytat": str(f.get("cytat"))[:240]})
        else:
            bad.append({**f, "powod": "cytat nie występuje w dokumencie"})
    c = config()
    return {"model": c["model"], "dostawca": c["provider"], "gdzie": c["where"], "sekundy": round(time.time() - t0, 1),
            "ryzyka": ok, "fakty": facts, "streszczenie": str(data.get("streszczenie", ""))[:800],
            "odrzucone": bad, "status": "propozycje modelu – do decyzji eksperta"}


def explain(project: dict) -> dict:
    ev = project.get("events") or []
    src = "\n".join(f"- {e.get('kod')}: {e.get('opis')} (ogłoszenie {e.get('ogloszenie')}, pole {e.get('pole')}): „{e.get('cytat')}”" for e in ev)
    user = (f"Projekt: {project.get('t')} ({project.get('n')}). Wartość umowy: {project.get('v')}, wypłacono: {project.get('paid')}, "
            f"zmiana kosztu: {project.get('cost')}%, po terminie: {project.get('late')}.\nZdarzenia z BZP:\n{src or '(brak)'}\n\n"
            'Wyjaśnij ekspertowi w 3–4 zdaniach, co się wydarzyło i co z tego wynika dla podobnych inwestycji. Zwróć JSON: {"tekst":"…","cytat":"dosłowny fragment jednego z cytatów"}')
    data = parse_json(chat(SYSTEM, user, max_tokens=600, timeout=60))
    quote_ok = bool(data.get("cytat")) and norm(data["cytat"]) in norm(src)
    return {"tekst": str(data.get("tekst", ""))[:900], "cytat": data.get("cytat") if quote_ok else None,
            "zweryfikowano": quote_ok, "model": config()["model"]}


def selftest() -> int:
    st = status()
    print(f"Tryb: {st['tryb']} · dostawca: {st['dostawca']} · model: {st['model'] or '—'} · {st['gdzie'] or ''}")
    if st["dostawca"] == "none":
        print("Model wyłączony. Dodaj do .env np.:  LLM_PROVIDER=mistral  i  MISTRAL_API_KEY=twój_klucz")
        return 1
    t0 = time.time()
    try:
        data = parse_json(chat("Odpowiadasz tylko poprawnym JSON.", 'Zwróć JSON: {"ok": true, "jezyk": "polski"}', max_tokens=200, timeout=90))
    except Exception as e:
        print(f"Błąd: {e}")
        return 1
    print(f"Odpowiedź w {time.time() - t0:.1f} s: {json.dumps(data, ensure_ascii=False)} – połączenie działa.")
    return 0


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        sys.exit(selftest())
    print(json.dumps(status(), ensure_ascii=False))
