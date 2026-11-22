#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html as htmlmod
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

import rm_ingest as ing
import rm_benchmark as bm
import rm_docs as docs
import rm_report as report
import rm_docx
import rm_llm as llm
import rm_config as cfg

CATALOG = {
    "R01": "Niepełny lub niespójny zakres", "R02": "Niedostateczne rozpoznanie stanu istniejącego",
    "R03": "Kolizje, dostęp do terenu i prawa do nieruchomości", "R04": "Decyzje, pozwolenia i uzgodnienia zależne od podmiotów trzecich",
    "R05": "Niejasny lub nieadekwatny podział odpowiedzialności", "R06": "Nierealistyczny harmonogram i nieuwzględnione zależności",
    "R07": "Nieprzewidziane zmiany i roboty dodatkowe", "R08": "Model wynagrodzenia niedopasowany do niepewności zakresu",
    "R09": "Niejednoznaczne lub niemierzalne wymagania techniczne", "R10": "Niedookreślone próby, rozruch i odbiór",
    "R11": "Postępowanie podatne na odwołanie lub ograniczenie konkurencji", "R12": "Niewystarczająca zdolność wykonawcy lub łańcucha dostaw",
    "R13": "Ryzyko finansowania, budżetu i płatności", "R14": "Nadmierne koszty eksploatacji i utrzymania",
    "R15": "Nieosiągnięcie efektu technologicznego, użytkowego lub środowiskowego"}

VOIV = [("dolnośląsk", "dolnośląskie"), ("kujawsko-pomorsk", "kujawsko-pomorskie"), ("lubelsk", "lubelskie"), ("lubusk", "lubuskie"),
        ("łódzk", "łódzkie"), ("małopolsk", "małopolskie"), ("mazowieck", "mazowieckie"), ("opolsk", "opolskie"),
        ("podkarpack", "podkarpackie"), ("podlask", "podlaskie"), ("zachodniopomorsk", "zachodniopomorskie"), ("pomorsk", "pomorskie"),
        ("dolnośląsk", "dolnośląskie"), ("śląsk", "śląskie"), ("świętokrzysk", "świętokrzyskie"), ("warmińsko-mazursk", "warmińsko-mazurskie"),
        ("wielkopolsk", "wielkopolskie")]

def read_text(path: Path) -> tuple[str, list[tuple[str, str]], bool]:
    ext = path.suffix.lower()
    if ext == ".doc":
        return docs.read_any(path)
    if ext in (".docx", ".pdf", ".txt", ".md"):
        fmt, units, ocr = docs.read_any(path)
        if ext in (".txt", ".md"):
            raw = path.read_bytes()
            for enc in ("utf-8", "cp1250", "iso-8859-2"):
                try:
                    t = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
            if len(paras) <= 2:
                paras = [l.strip() for l in t.splitlines() if l.strip()]
            units = [(f"akapit {i}", p) for i, p in enumerate(paras, 1)]
        return fmt, units, ocr
    if ext == ".odt":
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("content.xml"))
        ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
        paras = ["".join(e.itertext()).strip() for e in root.iter() if e.tag in (ns + "p", ns + "h")]
        return "ODT", [(f"akapit {i}", p) for i, p in enumerate([p for p in paras if p], 1)], False
    if ext in (".html", ".htm"):
        t = path.read_text(encoding="utf-8", errors="replace")
        t = re.sub(r"(?is)<(script|style).*?</\1>", " ", t)
        t = re.sub(r"(?i)</(p|div|h\d|li|tr|br)\s*>|<br\s*/?>", "\n\n", t)
        t = htmlmod.unescape(re.sub(r"<[^>]+>", " ", t))
        paras = [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
        return "HTML", [(f"akapit {i}", p) for i, p in enumerate(paras, 1)], False
    if ext == ".rtf":
        t = path.read_text(encoding="latin-1", errors="replace")
        t = re.sub(r"\\'([0-9a-f]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1250", "replace"), t)
        t = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), t)
        t = re.sub(r"\\par[d]?\b", "\n\n", t)
        t = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", t)
        paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
        return "RTF", [(f"akapit {i}", p) for i, p in enumerate(paras, 1)], False
    if ext == ".json":
        d = json.loads(path.read_text(encoding="utf-8"))
        flat = []
        def walk(x, k=""):
            if isinstance(x, dict):
                for a, b in x.items():
                    walk(b, a)
            elif isinstance(x, list):
                for b in x:
                    walk(b, k)
            elif x not in (None, ""):
                flat.append(f"{k}: {x}" if k else str(x))
        walk(d)
        return "JSON", [(f"pole {i}", p) for i, p in enumerate(flat, 1)], False
    t = path.read_bytes().decode("utf-8", "replace")
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    return "tekst", [(f"akapit {i}", p) for i, p in enumerate(paras, 1)], False


ABBR = r"\b(ok|r|ul|al|pl|os|m\.in|np|tj|tzw|tys|mln|mld|nr|ww|pkt|ust|art|godz|dz|woj|pow|gm|św|im|ha|mb|szt|zł|wg|ok|pn|ds|sp|o\.o|p\.p|kw|ew|lit|poz|str|tab|rys|zał|dot|mies|m|km|n\.p\.m|ul)\."


def sentences(units: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out = []
    for loc, txt in docs.paragraphs(units):
        flat = re.sub(r"\s+", " ", txt).strip()
        flat = re.sub(r"\b(r|tys|mln|mld|zł|km|m|mies|szt|mb|ha|godz)\.(?=\s+[a-ząćęłńóśźż0-9(])", lambda m: m.group(0)[:-1] + "\u2024", flat)
        flat = re.sub(r"\b(ok|ul|al|pl|os|m\.in|np|tj|tzw|nr|ww|pkt|ust|art|dz|woj|pow|gm|św|im|wg|pn|ds|sp|o\.o|kw|ew|lit|poz|str|tab|rys|zał|dot|n\.p\.m)\.(?=\s)", lambda m: m.group(0)[:-1] + "\u2024", flat, flags=re.I)
        flat = re.sub(r"(\d)\.(?=\s+\d)", "\\1\u2024", flat)
        for s in re.split(r"(?<=[.;!?])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ0-9„\"(–-])", flat):
            s = s.replace("\u2024", ".").strip()
            if len(s) >= 3:
                out.append((loc, s))
    return out


def low(s: str) -> str:
    return (s or "").lower().replace("\xa0", " ")


def ev(loc: str, s: str, n: int = 300) -> dict:
    return {"miejsce": loc, "cytat": s if len(s) <= n else s[:n - 1] + "…"}


NUM = r"\d{1,3}(?:[ \xa0.]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"


def to_float(s: str) -> float | None:
    s = s.replace("\xa0", " ").strip()
    if re.fullmatch(r"\d{1,3}(?:[ .]\d{3})+(?:,\d+)?", s):
        s = s.replace(" ", "").replace(".", "").replace(",", ".")
    else:
        s = s.replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def amounts(s: str) -> list[tuple[float, str]]:
    out = []
    for m in re.finditer(rf"({NUM})\s*(mld|miliard\w*|mln|milion\w*|tys\.?|tysi\w*)?\s*(zł|pln|złotych)\b", low(s)):
        v = to_float(m.group(1))
        if v is None:
            continue
        mul = m.group(2) or ""
        v *= 1e9 if mul.startswith(("mld", "miliard")) else 1e6 if mul.startswith(("mln", "milion")) else 1e3 if mul.startswith("tys") else 1
        out.append((v, m.group(0)))
    return out


def extract_facts(db: ing.Db | None, sents: list[tuple[str, str]], units) -> dict:
    full = "\n".join(s for _, s in sents)
    F: dict = {}

    def put(k, v, loc, s):
        if v not in (None, "", []) and k not in F:
            F[k] = {"wartosc": v, **ev(loc, s)}

    for loc, s in sents:
        m = re.match(r"(?i)(?:nazwa|tytuł)\s+(?:zadania|inwestycji|przedsięwzięcia|projektu)\s*[:\-–]\s*(.+)", s)
        if m:
            put("nazwa", m.group(1).strip(" .„”\""), loc, s)
            break
    if "nazwa" not in F:
        cand = [(loc, l) for loc, txt in units[:6] for l in (txt.splitlines() or [txt])]
        for loc, l in cand[:12]:
            t = re.sub(r"\s+", " ", l).strip(" #*\t")
            if 12 <= len(t) <= 220 and not t.endswith(":") and not re.match(r"(?i)(opis|spis|strona|str\.)\b", t):
                put("nazwa", t.strip(" .„”\""), loc, t)
                break
    lens = []
    for loc, s in sents:
        l = low(s)
        m = re.search(r"\b((?:Miasto i Gmina|Gmina Miejska|Gmina Miasto|Gmina|Miasto)\s+[A-ZŁŚŻŹĆÓ][\wąćęłńóśźż-]+(?:[ -][A-ZŁŚŻŹĆÓ][\wąćęłńóśźż-]+)?)", s)
        if m and "inwestor" not in F and not re.search(r"(?i)województw|powiat", m.group(1)):
            put("inwestor", m.group(1), loc, s)
        m = re.search(r"(?i)inwestor\w*\s*(?:jest|będzie|:|–|-)\s*([^.;,]{4,80})", s)
        if m:
            F["inwestor"] = {"wartosc": m.group(1).strip(), **ev(loc, s)}
        m = re.search(r"((?:Przedsiębiorstw\w*|Zakład\w*|Spółk\w*|Miejsk\w*|Gminn\w*|Komunaln\w*)[^.;,()]{0,80}?(?:Wodociąg\w*|Komunaln\w*|Gospodarki\s+Komunalnej|Wod\w*)[^.;,()]{0,40}?(?:sp\.\s*z\s*o\.\s*o\.|S\.A\.)?)|\b(ZGK|PWiK|PGK|ZUK|ZWiK|PWK|MPWiK|ZGKiM)\b", s)
        if m and ("operator" in l or "eksploat" in l or "przedsiębiorstw" in l or "zakład" in l):
            put("operator", (m.group(1) or m.group(2)).strip(), loc, s)
        m = re.search(r"(?i)\bNIP\W{0,3}([\d][\d\- ]{8,12}\d)", s)
        if m:
            put("nip", re.sub(r"\D", "", m.group(1)), loc, s)
        for stem, name in VOIV:
            if re.search(rf"(?<![\w-]){stem}\w*", l) and re.search(r"wojew|woj\.", l):
                if stem == "pomorsk" and re.search(r"(kujawsko|zachodnio)-?pomorsk", l):
                    continue
                if stem == "śląsk" and "dolnośląsk" in l:
                    continue
                put("wojewodztwo", name, loc, s)
                break
        m = re.search(r"(?i)powi\w*\s+([a-ząćęłńóśźż-]{4,}(?:sk\w+|ck\w+|dzk\w+))", s)
        if m:
            put("powiat", m.group(1).lower(), loc, s)
        m = re.search(r"(?:w miejscowości|w miejscowościach|we wsi|w sołectwie|w sołectwach|miejscowości)\s+([A-ZŁŚŻŹĆÓ][^.;:()]{2,120})", s)
        if m:
            put("miejscowosci", re.sub(r"\s+(?:w gminie|na terenie|gmin[ay]).*$", "", m.group(1)).strip(" ,"), loc, s)

        if re.search(r"długoś|sieć|sieci|przewod|wodociąg|kanał|kanalizac|odcin|rurociąg|magistral|tras", l):
            for m in re.finditer(rf"({NUM})\s*(km|kilometr\w*|mb|m\.b\.|metr\w*|m)(?![²³2-3a-ząćęłńóśźż/])", l):
                v = to_float(m.group(1))
                if not v:
                    continue
                km = v if m.group(2).startswith("k") else v / 1000 if v >= 50 else None
                if km and 0.05 <= km <= 400:
                    whole = 2 if re.search(r"łączn|całkowit|ogółem|w sumie|razem", l) else 1 if re.search(r"długoś|sieć|sieci", l) else 0
                    lens.append((whole, km, loc, s))
        m = re.search(r"\b(?:DN|Ø|fi|średnic\w*)\s*(\d{2,4})", s, re.I)
        if m:
            put("srednica_mm", int(m.group(1)), loc, s)
        m = re.search(rf"({NUM})\s*(?:szt\.?\s*)?(?:przyłącz\w*|odgałęzie\w* do posesji)", l) or re.search(rf"przyłącz\w*[^.;]{{0,40}}?(\d+)\s*(?:szt|nieruchomo|budynk|posesj)", l)
        if m:
            put("przylacza", int(to_float(m.group(1)) or 0) or None, loc, s)
        m = re.search(rf"({NUM})\s*(budynk\w*|dom\w*|nieruchomoś\w*|gospodarstw\w*|posesj\w*|działek|lokali)", l)
        if m:
            put("budynki", int(to_float(m.group(1)) or 0) or None, loc, s)
        m = re.search(rf"({NUM})\s*(mieszkańc\w*|osób|osoby|RLM)\b", l, re.I)
        if m:
            put("mieszkancy", int(to_float(m.group(1)) or 0) or None, loc, s)
        m = re.search(rf"({NUM})\s*(m3|m³)\s*/\s*(d|dobę|h|godz)", l)
        if m:
            put("wydajnosc", m.group(0), loc, s)

        if re.search(r"wartoś|koszt|kosztorys|nakład|budżet|szacun|wycen|kwot", l) and not re.search(r"dofinansowan\w* w wysokości|dotacj\w* w wysokości", l):
            am = amounts(s)
            if am:
                v, raw = max(am)
                cur = F.get("koszt", {}).get("wartosc", {}).get("kwota", 0)
                if v > cur and v >= 10000:
                    rodz = "netto" if "netto" in l else "brutto" if "brutto" in l or "z vat" in l else "nie podano (netto/brutto)"
                    F["koszt"] = {"wartosc": {"kwota": v, "rodzaj": rodz, "tekst": raw}, **ev(loc, s)}
        m = re.search(r"dofinansowan\w*[^.;]{0,80}?(\d{1,3})\s*%", l)
        if m:
            put("dofinansowanie_pct", int(m.group(1)), loc, s)
        src = [n for k, n in (("kpo", "KPO"), ("krajow\\w* plan\\w* odbudowy", "KPO"), ("feniks", "FEnIKS"), ("fundusz\\w* europejsk\\w* na infrastruktur", "FEnIKS"),
                              ("rpo|program\\w* regionaln|fundusze europejskie dla", "program regionalny"), ("polski ład|program\\w* inwestycji strategicznych|rpis", "Rządowy Program Inwestycji Strategicznych"),
                              ("rfil|rządow\\w* fundusz\\w* inwestycji lokalnych", "RFIL"), ("wfoś|wojewódzk\\w* fundusz\\w* ochrony", "WFOŚiGW"),
                              ("nfoś|narodow\\w* fundusz\\w* ochrony", "NFOŚiGW"), ("prow\\b|program\\w* rozwoju obszarów wiejskich", "PROW"),
                              ("środk\\w* własn|budżet\\w* gminy", "środki własne"), ("pożyczk|kredyt|obligacj", "dług (pożyczka/kredyt)")) if re.search(k, l)]
        for n in src:
            F.setdefault("finansowanie", {"wartosc": [], **ev(loc, s)})
            if n not in F["finansowanie"]["wartosc"]:
                F["finansowanie"]["wartosc"].append(n)
        m = re.search(r"(?:w latach|lata|okres)\s*(20\d\d)\s*[–\-—/]\s*(20\d\d)", l)
        if m and "czas_mies" not in F:
            put("czas_mies", (int(m.group(2)) - int(m.group(1)) + 1) * 12, loc, s)
            put("lata", f"{m.group(1)}–{m.group(2)}", loc, s)
        m = re.search(rf"({NUM})\s*(miesi\w*|mies\.)", l)
        if m and re.search(r"realizac|robót|robot|wykonan|budow|czas|termin|trwa|harmonogram|okres", l):
            v = to_float(m.group(1))
            if v and 1 <= v <= 120:
                F["czas_mies"] = {"wartosc": v, **ev(loc, s)}
        m = re.search(r"\b(?:zakończeni\w*|zakończy\w*|oddani\w*|uruchomieni\w*|do końca|do)\s+(?:[^.;]{0,30}?\s)?(?:roku\s+|r\.\s+)?(20\d\d)\b", l)
        if m and not re.search(r"badani|wykonan\w* w|w roku 20\d\d przeprowadz|ankiet", l[:m.start()+1][-60:]):
            put("rok_zakonczenia", int(m.group(1)), loc, s)
        m = re.search(r"(?:rozpocz\w*|start|ogłoszeni\w* przetargu|przetarg)[^.;]{0,50}?(20\d\d)", l)
        if m:
            put("rok_rozpoczecia", int(m.group(1)), loc, s)
        if re.search(r"zaprojektuj i wybuduj|zaprojektuj-wybuduj|programu funkcjonalno|\bpfu\b|design\s*(&|and)\s*build", l):
            put("formula", "zaprojektuj i wybuduj (PFU)", loc, s)
        elif re.search(r"(na podstawie|w oparciu o)\s+(projekt|dokumentacj)|wybuduj\b|tradycyjn", l):
            put("formula", "wybuduj (na podstawie dokumentacji projektowej)", loc, s)
        m = re.search(r"(ryczałt\w*|kosztorysow\w*)", l)
        if m and "wynagrodz" in l:
            put("wynagrodzenie", "ryczałtowe" if m.group(1).startswith("ryczał") else "kosztorysowe", loc, s)
        if re.search(r"przebudow|modernizac|renowac|wymian", l):
            put("rodzaj_prac", "przebudowa / modernizacja", loc, s)
        elif re.search(r"\bbudow|rozbudow", l):
            put("rodzaj_prac", "budowa / rozbudowa", loc, s)

    if lens:
        w, km, loc, s = max(lens, key=lambda x: (x[0], x[1]))
        F["dlugosc_km"] = {"wartosc": round(km, 2), **ev(loc, s)}
        if len({round(x[1], 2) for x in lens}) > 1:
            F["dlugosc_km"]["inne_odcinki_km"] = sorted({round(x[1], 2) for x in lens if round(x[1], 2) != round(km, 2)})
    title = (F.get("nazwa") or {}).get("wartosc", "")
    keys = {"Sieć wodociągowa": r"wodociąg|sieci? wod(?!no)|przewod\w* wod|wody pitnej|zaopatrzeni\w* w wodę",
            "Kanalizacja sanitarna": r"kanalizac\w* sanitar|ściek\w*(?! deszcz)|przepompowni\w* ściek|tłoczn|kanalizac(?!\w* deszcz)",
            "Oczyszczalnia ścieków": r"oczyszczal", "Uzdatnianie i zaopatrzenie w wodę": r"uzdatnian|\bsuw\b|ujęci\w* wody|studni\w* głębin|zbiornik\w* wody|hydrofor",
            "Kanalizacja deszczowa i retencja": r"deszczow|retenc|wód opadow|odwodnieni"}
    sc = {k: len(re.findall(p, low(full))) + 3 * len(re.findall(p, low(title))) for k, p in keys.items()}
    best = max(sc, key=sc.get)
    F["podsektor"] = {"wartosc": best if sc[best] else bm.subsector(title), "miejsce": "cały tekst",
                      "cytat": "liczba odwołań: " + ", ".join(f"{k} {v}" for k, v in sorted(sc.items(), key=lambda x: -x[1]) if v)}
    extra = [k for k, v in sorted(sc.items(), key=lambda x: -x[1]) if k != F["podsektor"]["wartosc"] and v >= max(2, sc[best] * .3)]
    if extra:
        F["podsektory_dodatkowe"] = {"wartosc": extra, "miejsce": "cały tekst", "cytat": ""}

    if "wojewodztwo" not in F and db is not None and F.get("inwestor"):
        g = re.sub(r"^(Miasto i Gmina|Gmina Miejska|Gmina Miasto|Gmina|Miasto)\s+", "", F["inwestor"]["wartosc"]).strip()
        try:
            r = db.c.execute("SELECT wojewodztwo, COUNT(*) c FROM umowy_sektor WHERE zamawiajacy LIKE ? AND wojewodztwo IS NOT NULL "
                             "GROUP BY wojewodztwo ORDER BY c DESC LIMIT 1", (f"%{g}%",)).fetchone()
            if r:
                F["wojewodztwo"] = {"wartosc": r[0], "miejsce": "baza BZP", "cytat": f"ustalone z ogłoszeń zamawiającego „{g}” w BZP (n = {r[1]}) – tekst nie podaje województwa",
                                    "pewnosc": "średnia"}
        except Exception:
            pass
    return F


OPEN = (r"nie (zostało|został|została|zostały)?\s*(jeszcze\s*)?(ustalon|rozpoznan|uzyskan|określon|potwierdzon|przeprowadzon|wykonan|uzgodnion|zbadan|sprawdzon)\w*|"
        r"nie (ustalono|rozpoznano|uzyskano|określono|potwierdzono|przeprowadzono|wykonano|uzgodniono|zbadano|sprawdzono|wiadomo|wiemy)|"
        r"do (ustalenia|uzgodnienia|wyjaśnienia|potwierdzenia|rozstrzygnięcia|decyzji)|w trakcie|na etapie (ustaleń|rozmów|uzgodnień)|ustnie|telefoniczn|"
        r"nie jest (jeszcze )?(znan|pewn|przesądzon)|brak (pisemn\w* )?(stanowiska|informacji|danych|decyzji|zgody|zgód|uzgodnień|potwierdzenia|rozpoznania|badań)|"
        r"nieznan|niepewn|nie przesądz|zostanie (ustalon|określon|rozpoznan|wyjaśnion|zbadan)|planuje się (ustalić|wystąpić|zlecić)|"
        r"wymaga (dalszego|dodatkowego|szczegółowego) (rozpoznania|ustalenia)|jeszcze nie|dopiero|otwart\w* kwesti|kwestią otwartą|nie wiadomo|wstępnie|orientacyjn|przypuszcz|prawdopodob")

CHECKLIST = [
    {"id": "1", "punkt": "Konkretny problem zaopatrzenia w wodę i oczekiwany efekt inwestycji.", "rozdzial": "1.1–1.2", "ryzyka": ["R15"],
     "elementy": [("problem", r"szamb|zbiornik\w* bezodpływ|przydomow\w* oczyszczal|nieszczeln|aglomeracj|dyrektyw|kpośk|podtopi|zalewan|brak\w* (dostępu|sieci|wodociąg|wody|kanalizac)|niedobor|przerw\w* w (dostaw|zasilani)|spad\w* ciśnieni|studni\w* (przydomow|indywidualn|kopan)|indywidualn\w* ujęć|jakoś\w* wody|zanieczyszcz|problem|nie (ma|mają) dostępu|niewystarczając"),
                  ("oczekiwany efekt", r"efekt|umożliwi|zapewni|poprawi|cel\w* (inwestycji|zadania|przedsięwzięcia)|dostęp\w* do (wody|sieci)|pewnoś\w* (zasilania|dostaw)|korzyś")],
     "uzupelnienie": "opis problemu z danymi: zgłoszenia mieszkańców, wyniki badań jakości wody, rejestr przerw i spadków ciśnienia"},
    {"id": "2", "punkt": "Obszar obsługi oraz przybliżona liczba i charakter odbiorców.", "rozdzial": "1.3–1.4", "ryzyka": ["R01", "R15"],
     "elementy": [("obszar obsługi", r"miejscowoś|\bulic|\bul\.|osiedl|obszar\w* (obsługi|inwestycji)|sołectw|\bwsi\b|\bwieś|teren\w* (inwestycji|obsługi)"),
                  ("liczba odbiorców (z jednostką)", rf"({NUM})\s*(budynk|dom|nieruchomo|gospodarstw|mieszkańc|odbiorc|posesj|osób|lokal|działek)"),
                  ("charakter odbiorców", r"jednorodzinn|wielorodzinn|zagrodow|usług|szkoł|przedszkol|zakład\w* (produkc|przetwór|przemysł|pracy)|obiekt\w* (publiczn|użyteczności)|gospodarstw\w* roln|letnisk|przedsiębior|ferm|hotel|pensjonat|odbiorc\w* (dominuj|duż|przemysł)|mieszkaniow")],
     "uzupelnienie": "wykaz nieruchomości w obszarze obsługi z rozróżnieniem: mieszkańcy / budynki / gospodarstwa oraz odbiorcy o dużym poborze"},
    {"id": "3", "punkt": "Przewidywani odbiorcy początkowi i wiarygodne przesłanki dalszego przyłączania nieruchomości.", "rozdzial": "2.1–2.3", "ryzyka": ["R15", "R14"],
     "elementy": [("odbiorcy początkowi", r"początkow|w pierwszym (roku|okresie|etapie)|od razu|deklaracj|zainteresowan|wnios\w* o (przyłącz|warunki)|ankiet|zgłosił|zgłoszeń|chęć przyłączenia|gotow\w* do przyłącz"),
                  ("przesłanki dalszego przyłączania", r"plan\w* miejscow|mpzp|now\w* zabudow|działk\w* (budowlan|przeznaczon)|pozwole\w* na budowę (dom|budynk)|rozwój|rozwoju|perspektyw|docelow|wzrost\w* liczby|parcelac|deweloper")],
     "uzupelnienie": "lista deklaracji / wniosków o przyłączenie z rozróżnieniem zamiaru i uzgodnionego przyłącza; przesłanki rozwoju (MPZP, pozwolenia na budowę)"},
    {"id": "4", "punkt": "Orientacyjny przebieg i długość sieci oraz zakres przewidywanych przyłączy.", "rozdzial": "3.1–3.4", "ryzyka": ["R01", "R07"],
     "elementy": [("przebieg", r"przebieg|tras[aęy]\b|trasą|wzdłuż|odcin|odgałęzi|od skrzyżowania|do granicy|w pas\w* drog|w ulic"),
                  ("długość", rf"({NUM})\s*(km|m|mb|metr)\b"), ("przyłącza", r"przyłącz")],
     "uzupelnienie": "szkic trasy na mapie z długością odcinków i liczbą przyłączy; rozstrzygnięcie, czy zakres obejmuje przyłącza do budynków"},
    {"id": "5", "punkt": "Przewidywane miejsce zasilania i wstępne stanowisko operatora o możliwości dostawy wody.", "rozdzial": "4.1–4.4", "ryzyka": ["R04", "R15"],
     "elementy": [("miejsce zasilania / włączenia", r"odprowadz\w* do|kolektor|oczyszczal\w*|odbiornik|włącz\w*|zasilan\w*|zasilona|zasilony|punkt\w* (włączenia|przyłączenia)|istniejąc\w* (sieć|sieci|wodociąg|przewod|magistral)|\bsuw\b|stacj\w* uzdatniania|ujęci\w* wody"),
                  ("stanowisko operatora", r"możliwoś\w* (odbioru|przyjęcia) ścieków|warunk\w* (odprowadzania|przyjęcia)|stanowisk\w* (operatora|przedsiębiorstwa|zakładu|eksploatatora|spółki)|warunk\w* (techniczn|przyłączenia|zasilania)|zapewni\w* dostaw|potwierdzi\w* (możliwoś|dostęp)|możliwoś\w* (zasilenia|dostawy|przyłączenia)|pism\w* (operatora|przedsiębiorstwa|zakładu|spółki|z dnia)|notatk|opini\w* (operatora|przedsiębiorstwa|zakładu)"),
                  ("wydajność / ciśnienie", r"wydajnoś|m3|m³|rezerw\w* (wydajności|wody|mocy)|ciśnieni|przepustowoś|zapotrzebowani\w* na wodę|przeciwpożar")],
     "uzupelnienie": "pisemne stanowisko operatora (zakres, data, liczba i rodzaj odbiorców, warunki) lub warunki techniczne; dane o wydajności dostępnej i ciśnieniu w rejonie włączenia"},
    {"id": "6", "punkt": "Wstępna trasa sieci, rodzaj własności gruntów i znane trudności z dostępem.", "rozdzial": "5.1–5.4", "ryzyka": ["R03", "R04"],
     "elementy": [("trasa", r"przebieg|tras[aęy]\b|trasą|wzdłuż|korytarz|w pas\w* drog|w ulic"),
                  ("własność gruntów", r"grunt\w* (gminn|prywatn|publiczn|skarbu|kolejow|leśn|państw)|własnoś|działk\w* (gminn|prywatn|ewidencyjn)|pas\w* drog|ewidencj|właściciel|zarządc\w* drog|nieruchomoś\w* (gminn|prywatn)"),
                  ("trudności z dostępem", r"sprzeciw|odm[óo]w|nie wyraził|nieuregulowan|trudnoś|dostęp\w* do (terenu|działk|nieruchomo)|służebnoś|zgod\w* właściciel|bez (problem|przeszkód)|nie (ma|występują) (przeszk|problem)")],
     "uzupelnienie": "wykaz działek na trasie z formą własności (wypis z ewidencji), stanowiska zarządców dróg i właścicieli, sposób uregulowania dostępu (służebność przesyłu)"},
    {"id": "7", "punkt": "Główne przeszkody lokalizacyjne i zależności od innych inwestycji.", "rozdzial": "5.4; 6.1–6.2", "ryzyka": ["R02", "R03", "R06"],
     "elementy": [("przeszkody terenowe", r"kolej|tor[óy]w|\bciek|\brzek|potok|\brów\b|rowu|row\w* melioracyjn|drog\w* (krajow|wojewódzk|ekspresow|powiatow)|uzbrojeni|kolizj|zbocz|skarp|podmokł|przeszkod|przewiert|przecisk|gazociąg|wąsk\w* ulic"),
                  ("zależności od innych inwestycji", r"zależ|uzależnion|po (wybudowaniu|realizacji|zakończeniu|przebudowie)\s+(?!sieci\b|sieć\b|wodociągu\b|inwestycji\b|zadania\b)|równolegle|skoordynow|koordynac|przebudow\w* drog|inn\w* (inwestycj|zadani|podmiot)|deweloper|inwestor\w* prywatn|wcześniejsz\w* (realizacji|budow|wzmocnieni)"),
                  ("planowanie przestrzenne", r"plan\w* miejscow|mpzp|decyzj\w* o (lokalizacji|warunkach zabudowy)|lokalizacji inwestycji celu publicznego|ulicp|studium|plan\w* ogóln")],
     "uzupelnienie": "mapa do celów projektowych / mapa uzbrojenia z zaznaczonymi przeszkodami; wypis z MPZP lub ścieżka lokalizacyjna; lista powiązanych zadań z podmiotem i terminem"},
    {"id": "8", "punkt": "Wstępne rozpoznanie potrzeby decyzji środowiskowej i ograniczeń ochrony przyrody.", "rozdzial": "6.3–6.5", "ryzyka": ["R04", "R06"],
     "elementy": [("potrzeba decyzji środowiskowej", r"decyzj\w* (o )?środowiskow|środowiskowych uwarunkowa|\bdśu\b|\bdśu\b|ocen\w* oddziaływania|raport\w* (o )?oddziaływ|\bkip\b|karta informacyjna|kwalifikac\w* przedsięwzięci|nie wymaga (uzyskania )?decyzji|magistral|rozdzielcz"),
                  ("ochrona przyrody", r"natura 2000|obszar\w* chronion|rezerwat|park\w* (krajobraz|narodow)|siedlisk|zadrzewie|drzew|ochron\w* przyrody|korytarz\w* ekologiczn|obszar\w* (cenn|przyrodnicz)|nie (występują|ma) (obszar|form)"),
                  ("wymagania wodnoprawne (gdy jest ciek)", r"wodnoprawn|pozwoleni\w* wodn|zgłoszeni\w* wodn|wody polskie|przekroczeni\w* (cieku|rzeki|rowu)")],
     "uzupelnienie": "wstępna kwalifikacja przedsięwzięcia (magistrala czy sieć rozdzielcza) z konsultacją w urzędzie; sprawdzenie Geoserwisu GDOŚ (Natura 2000, formy ochrony); rozpoznanie wymagań wodnoprawnych dla przekroczeń cieków"},
    {"id": "9", "punkt": "Podział odpowiedzialności za przygotowanie, projekt, nieruchomości, decyzje i eksploatację oraz główne ryzyka.", "rozdzial": "7.1–7.5", "ryzyka": ["R05", "R14"],
     "elementy": [("prowadzenie inwestycji", r"inwestor\w*|prowadz\w* (inwestycj|zadani|przedsięwzię|postępowani)|koordynuj|odpowiad\w* za"),
                  ("dokumentacja projektowa", r"projektant|dokumentacj\w* projektow|projekt\w* (budowlan|techniczn|wykonawcz)|zaprojektuj|\bpfu\b|biur\w* projektow|koncepcj\w* (programow|techniczn)"),
                  ("nieruchomości i decyzje", r"rozmow\w* z właściciel|uzgodnie\w* z (właściciel|zarządc)|wnios\w* o (decyzj|pozwoleni|zgod)|uzyskani\w* (decyzj|pozwoleni|zgód)|pozwoleni\w* na budowę|zgłoszeni\w* robót|służebnoś"),
                  ("właściciel i eksploatacja sieci", r"eksploatac|utrzymani\w* sieci|właściciel\w* (sieci|infrastruktur|majątk)|przekazan\w* (sieci|majątk|infrastruktur)|awari|aport|dzierżaw|operator\w* (sieci|będzie)"),
                  ("główne ryzyka", r"ryzyk|niepewnoś|zagrożen")],
     "uzupelnienie": "macierz odpowiedzialności (gmina / operator / projektant / właściciele / organy) dla: projektu, nieruchomości, decyzji, eksploatacji; lista głównych niepewności z podmiotem odpowiedzialnym"},
]


def checklist(sents: list[tuple[str, str]]) -> list[dict]:
    out = []
    for c in CHECKLIST:
        el_out, found_n, open_ev = [], 0, []
        for name, pat in c["elementy"]:
            hits = [(loc, s) for loc, s in sents if re.search(pat, low(s))]
            def strength(x):
                return sum(bool(re.search(p, low(x[1]))) for _, p in c["elementy"]) + min(len(x[1]), 300) / 1000
            hits.sort(key=lambda x: -strength(x))
            optional = name.startswith("wymagania wodnoprawne")
            if hits:
                found_n += 1
                op = [h for h in hits[:2] if re.search(OPEN, low(h[1])) and re.search(pat, low(h[1]))]
                open_ev += op[:1]
                el_out.append({"element": name, "status": "ujęte, sprawa otwarta" if op else "ujęte",
                               "dowody": [ev(*h, n=260) for h in hits[:2]], "otwarte": [ev(*h, n=260) for h in op[:1]]})
            else:
                el_out.append({"element": name, "status": "nie dotyczy / brak cieku" if optional else "opis nie wspomina", "dowody": [], "otwarte": []})
        need = [e for e, (n, _) in zip(el_out, c["elementy"]) if not n.startswith("wymagania wodnoprawne")]
        k = sum(e["status"] != "opis nie wspomina" for e in need)
        opened = any(e["status"] == "ujęte, sprawa otwarta" or e["otwarte"] for e in need)
        if k == 0:
            st, pts = "opis nie wspomina", 0
        elif k < len(need):
            st, pts = "częściowo", round(.5 * k / len(need) + (.2 if not opened else .1), 2)
        elif opened:
            st, pts = "ujęte, sprawa otwarta (brak potwierdzenia)", .7
        else:
            st, pts = "ujęte", 1.0
        brak = [e["element"] for e in need if e["status"] == "opis nie wspomina"]
        out.append({"id": c["id"], "punkt": c["punkt"], "rozdzial": c["rozdzial"], "status": st, "punkty": pts, "elementy": el_out,
                    "brakuje": brak, "ryzyka": c["ryzyka"], "uzupelnienie": c["uzupelnienie"] if st != "ujęte" else ""})
    return out


RISK_RULES = [
    ("R03", r"grunt\w* prywatn|własnoś\w* prywatn|działk\w* prywatn|przez (działk|posesj|nieruchomoś)\w* (prywatn|osób)|nieruchomoś\w* (osób fizycznych|prywatn)",
     "Trasa przebiega przez grunty prywatne", "Brak zgody właściciela lub spór o służebność na części trasy",
     "Zmiana trasy, wstrzymanie części robót, roszczenia, wydłużenie terminu", "Zgody / umowy służebności przesyłu przed ogłoszeniem przetargu; wariant trasy po gruntach publicznych", "średnia"),
    ("R03", r"sprzeciw|odm[óo]wi\w*|nie wyraził\w* zgody|nieuregulowan\w* (stan|stanie)|spór|sporn|brak zgody",
     "Znany sprzeciw właściciela lub nieuregulowany stan prawny", "Brak możliwości wejścia w teren na wskazanym odcinku",
     "Zmiana trasy lub technologii, opóźnienie, wzrost kosztu", "Rozstrzygnąć przed projektem: wariant omijający, regulacja stanu prawnego, ewentualnie art. 124 ugn", "średnia"),
    ("R04", r"pas\w* drog\w*|drog\w* (powiatow|wojewódzk|krajow|ekspresow)|zarządc\w* drog|gddkia|zarząd\w* dróg",
     "Sieć w pasie drogowym zarządzanym przez inny podmiot", "Odmowa lub warunki zarządcy drogi (lokalizacja, odtworzenie nawierzchni, opłaty)",
     "Opóźnienie, dodatkowe koszty odtworzenia, zmiana przebiegu", "Wczesne uzgodnienie lokalizacji z zarządcą drogi; koszt zajęcia pasa i odtworzenia w budżecie", "niska"),
    ("R04", r"kolej|\bpkp\b|tor[óy]w|linii kolejow",
     "Przejście pod linią kolejową", "Długa procedura uzgodnień z PKP PLK, wymagania technologiczne (przewiert, rura ochronna)",
     "Opóźnienie i wzrost kosztu przejścia", "Uzgodnić warunki przejścia z zarządcą linii przed przetargiem; przewidzieć technologię bezwykopową", "średnia"),
    ("R04", r"\bciek|\brzek|potok|row\w* melioracyjn|\browu\b|\brów\b|przekroczeni\w* (cieku|rzeki|rowu)|wody polskie",
     "Przekroczenie cieku lub rowu", "Konieczność zgody wodnoprawnej / zgłoszenia, ograniczenia terminów robót",
     "Opóźnienie rozpoczęcia, zmiana technologii przekroczenia", "Rozpoznać wymagania wodnoprawne i uzgodnić z Wodami Polskimi; wskazać miejsce przekroczenia", "niska"),
    ("R04", r"natura 2000|obszar\w* chronion|rezerwat|park\w* krajobraz|siedlisk|korytarz\w* ekologiczn",
     "Trasa w obszarze chronionym lub w jego pobliżu", "Obowiązek oceny oddziaływania lub warunki środowiskowe ograniczające prace",
     "Opóźnienie decyzji, zmiana trasy, ograniczenia terminów robót", "Wstępna kwalifikacja w RDOŚ/urzędzie; zaplanować czas na decyzję środowiskową", "średnia"),
    ("R04", r"magistral",
     "Przewód może mieć funkcję magistralną", "Przedsięwzięcie może wymagać decyzji o środowiskowych uwarunkowaniach",
     "Dodatkowa procedura i kilka miesięcy opóźnienia", "Ustalić funkcję przewodu (magistrala czy sieć rozdzielcza) i kwalifikację przed projektem", "niska"),
    ("R04", r"(brak|nie (uzyskano|ma|otrzymano)|bez)\s+(pisemn\w* )?(stanowiska|potwierdzenia|warunków|zgody|opinii)\w*[^.;]{0,40}(operator|przedsiębiorstw|zakład|spółk)|"
            r"(operator|przedsiębiorstw\w*|zakład\w*|spółk\w*)[^.;]{0,80}(ustnie|telefoniczn|nie potwierdził|nie wydał|nie przedstawił|jeszcze nie)",
     "Brak pisemnego stanowiska operatora o możliwości dostawy wody", "Operator nie zapewni wody w zakładanej ilości lub postawi warunki (wzmocnienie sieci)",
     "Sieć zbudowana bez zasilania, dodatkowy zakres, opóźnienie uruchomienia", "Uzyskać pisemne stanowisko lub warunki techniczne operatora przed projektem", "średnia"),
    ("R15", r"niedobor|ograniczeni\w* (poboru|dostaw)|spad\w* ciśnieni|niskie ciśnienie|niewystarczając\w* (ciśnieni|wydajnoś|ilość)|wyżej położon|wzniesieni|na wzgórzu|przepustowoś",
     "Rozpoznane ograniczenia dostawy (niedobory, ciśnienie, wysokość terenu)", "Niewystarczające ciśnienie lub ilość wody u nowych odbiorców",
     "Efekt użytkowy nieosiągnięty, konieczność hydroforni lub wzmocnienia sieci", "Obliczenia hydrauliczne z operatorem; ewentualna hydrofornia / wzmocnienie odcinka zasilającego w zakresie", "średnia"),
    ("R15", r"deklaracj|ankiet|zainteresowan|wstępn\w* zgłosz",
     "Liczba odbiorców opiera się na deklaracjach / zainteresowaniu", "Część właścicieli nie przyłączy się mimo dostępu do sieci",
     "Niski pobór, niższe przychody, ryzyko nieosiągnięcia wskaźników efektu", "Zbierać wnioski o warunki przyłączenia; rozważyć przyłącza w zakresie inwestycji; zachęty dla mieszkańców", "średnia"),
    ("R14", r"sezonow|letnisk|domk\w* (letnisk|rekreac)|okresow\w* (użytk|zamieszk)|pustostan|mały pobór|małym poborze|niewielk\w* (pobór|liczb\w* odbiorc)|rozproszon",
     "Mały lub sezonowy pobór na długim przewodzie", "Stagnacja wody, pogorszenie jakości, konieczność płukania sieci",
     "Wyższe koszty eksploatacji, skargi na jakość wody", "Omówić z operatorem: średnice, pętle, punkty płukania; etapowanie zakresu do istniejącej zabudowy", "niska"),
    ("R06", r"zależ\w* od|uzależnion|po (wybudowaniu|realizacji|zakończeniu|przebudowie)\s+(?!sieci\b|sieć\b|wodociągu\b|inwestycji\b|zadania\b)|równolegle z|wcześniejsz\w* (realizacji|budow|wzmocnieni|przebudow)|skoordynow",
     "Realizacja zależy od innego zadania lub podmiotu", "Opóźnienie powiązanego zadania wstrzymuje uruchomienie sieci",
     "Opóźnienie efektu, przestoje, roszczenia wykonawcy", "Wskazać podmiot i termin powiązanego zadania; kamienie milowe i klauzula zmiany terminu w umowie", "średnia"),
    ("R06", r"deweloper|inwestor\w* prywatn|osiedl\w* (planowan|projektowan|w budowie)|planowan\w* zakład",
     "Część odbiorców lub sieci zależy od inwestora prywatnego", "Osiedle / zakład nie powstanie w zakładanym terminie",
     "Przewymiarowana sieć, niski pobór, opóźniony efekt", "Porozumienie z inwestorem prywatnym; etapowanie zakresu", "niska"),
    ("R02", r"podmokł|grunt\w* słab|torf|wysok\w* (poziom|stan)\w* wód|wód gruntowych|skarp|zbocz|osuwisk|kamieni|skał",
     "Trudne warunki gruntowo-wodne na trasie", "Nieprzewidziane roboty (odwodnienie, wymiana gruntu, umocnienia)",
     "Wzrost kosztu i wydłużenie robót", "Badania geotechniczne przed przetargiem i ich udostępnienie wykonawcom", "średnia"),
    ("R02", r"uzbrojeni\w*|kolizj|gazociąg|kabl|sieci (energetyczn|telekomunikacyjn|gazow)|wąsk\w* (ulic|drog)",
     "Gęste uzbrojenie terenu lub możliwe kolizje", "Kolizje wykryte w trakcie robót, przebudowy innych sieci",
     "Roboty dodatkowe, przestoje, zmiana trasy", "Mapa do celów projektowych, inwentaryzacja uzbrojenia, uzgodnienia z gestorami sieci", "niska"),
    ("R02", r"stan\w* techniczn\w* (istniejąc|sieci|przewod)|stary|starej|azbest|żeliwn\w* (sieć|przewód)|awaryjn|nieinwentaryz|brak dokumentacji (istniejąc|powykonawcz)",
     "Niepewny stan istniejącej infrastruktury", "Zakres przebudowy większy niż zakładany",
     "Roboty dodatkowe, wzrost kosztu", "Inwentaryzacja i ocena stanu technicznego przed określeniem zakresu", "niska"),
    ("R01", r"zaprojektuj i wybuduj|programu funkcjonalno|\bpfu\b",
     "Formuła zaprojektuj i wybuduj na podstawie PFU", "Spór o zakres i parametry, zmiany po projekcie",
     "Roboty dodatkowe, aneksy, wzrost kosztu", "Doprecyzować PFU (ilości, parametry, granice), dołączyć inwentaryzację i badania", "niska"),
    ("R01", r"szacun\w*|orientacyjn|wskaźnikow|szacowan|przybliżon",
     "Zakres i koszt określone szacunkowo", "Rzeczywisty zakres po projekcie odbiega od koncepcji",
     "Zmiana budżetu, zmiana zakresu", "Aktualizacja kosztorysu po rozpoznaniu trasy; rezerwa w budżecie", "niska"),
    ("R08", r"ryczałt",
     "Wynagrodzenie ryczałtowe przy niepewnym zakresie", "Wykonawca wycenia ryzyko w cenie lub spór o zakres objęty ryczałtem",
     "Zawyżona cena lub roszczenia", "Ryczałt tylko dla dobrze określonego zakresu; ceny jednostkowe dla pozycji niepewnych", "niska"),
    ("R13", r"dofinansowan|dotacj|nabór|naboru|wniosek o dofinansowanie|\bkpo\b|feniks|polski ład|rządow\w* program|rfil|wfoś|nfoś|prow\b",
     "Finansowanie zależy od dotacji i jej terminów", "Brak dofinansowania lub niedotrzymanie terminu rozliczenia",
     "Ograniczenie zakresu, utrata części środków, presja na harmonogram", "Harmonogram zgodny z terminami programu; plan B finansowania; rezerwa na wkład własny", "niska"),
    ("R13", r"brak (zabezpieczonych )?środków|nie zabezpieczon|niewystarczając\w* środk|deficyt|zadłużeni",
     "Budżet nie jest w pełni zabezpieczony", "Brak środków na zakres lub zmiany",
     "Wstrzymanie lub ograniczenie inwestycji", "Zabezpieczyć środki w WPF; etapowanie", "średnia"),
    ("R05", r"kilku gmin|dwóch gmin|międzygmin|związ\w* (gmin|międzygmin)|porozumieni\w* (między|gmin)|dw[aóu]\w* (operator|system)|inn\w* operator|granic\w* gmin",
     "Inwestycja obejmuje kilka gmin lub operatorów", "Niejasny podział własności, eksploatacji i dostawy wody",
     "Spór o koszty i odpowiedzialność, opóźnione przekazanie do eksploatacji", "Porozumienie międzygminne / umowa operatorów przed realizacją", "średnia"),
    ("R05", r"nie (ustalono|określono|wiadomo)[^.;]{0,60}(eksploat|właściciel|przekazan|operator|kto)|(eksploatac|właściciel\w* sieci|przekazani\w* (sieci|majątk))[^.;]{0,80}(do ustalenia|nie ustalon|otwart|nie określon)",
     "Nieustalony właściciel lub eksploatator nowej sieci", "Sieć po odbiorze nie ma przypisanego eksploatatora",
     "Opóźnione uruchomienie, spór o koszty utrzymania", "Ustalić właściciela i operatora sieci oraz sposób przekazania majątku", "średnia"),
    ("R09", r"przeciwpożar|hydrant",
     "Sieć ma pełnić funkcję przeciwpożarową", "Wymagania przeciwpożarowe (wydajność, ciśnienie) nie zostaną spełnione",
     "Brak odbioru lub dodatkowe roboty", "Wpisać mierzalne parametry przeciwpożarowe do PFU/projektu i uzgodnić z operatorem", "niska"),
    ("R10", r"rozruch|\bpróby|płukani|dezynfekc|badani\w* (jakości )?wody (przed|po)|odbiór końcow|sanepid|\bpis\b|stacj\w* sanitarn",
     "Odbiór sieci wymaga prób, płukania i badań wody", "Opóźnienie odbioru i rozpoczęcia dostaw",
     "Opóźnione użytkowanie i rozliczenie", "Program prób i odbioru z kryteriami w umowie; czas na badania wody w harmonogramie", "niska"),
    ("R11", r"jedn\w* ofert|mał\w* konkurencj|unieważni|odwołani\w* do kio|kio\b|powtórzon\w* postępowan",
     "Sygnały słabej konkurencji lub unieważnień", "Unieważnienie lub powtórzenie postępowania",
     "Wydłużenie przygotowania, wyższa cena", "Analiza rynku, podział na części, realistyczne warunki udziału", "średnia"),
    ("R06", r"(do|w)\s+(\d{1,2})\s*(miesi|mies\.)|krótki\w* termin|pilnie|w tym samym roku|do końca (bieżącego )?roku",
     "Krótki lub sztywny termin realizacji", "Kumulacja prac, presja na skrócenie prób",
     "Opóźnienie, kary, gorsza jakość", "Porównać termin z medianą czasu podobnych umów z BZP; rezerwa czasowa", "niska"),
]


def risk_signals(sents: list[tuple[str, str]]) -> list[dict]:
    out, per = [], {}
    for kod, pat, war, zd, wp, srod, pew in RISK_RULES:
        hits = [(loc, s) for loc, s in sents if re.search(pat, low(s))]
        if not hits:
            continue
        hits.sort(key=lambda h: -len(re.findall(pat, low(h[1]))))
        key = (kod, war)
        neg = [h for h in hits if re.search(r"\b(nie (występuj\w*|przebiega|ma|stwierdzono|przewiduje się|znajduje się|koliduje)|brak\w*|poza)\b[^.;,]{0,40}(?:" + pat + ")", low(h[1]))
               and not re.search(r"(brak|nie)\w*\s+(pisemn|stanowisk|zgod|potwierdz|uzgodni|decyzj)", low(h[1]))]
        real = [h for h in hits if h not in neg]
        if not real:
            out.append({"kod": kod, "nazwa": CATALOG[kod], "sygnal": war, "zdarzenie": zd, "wplyw": wp, "srodki": srod, "pewnosc": "informacja",
                        "uwaga": "Opis wprost wyklucza ten sygnał – zachowane jako informacja.", "dowody": [ev(*h, n=280) for h in neg[:2]], "zrodlo": "treść opisu"})
            continue
        if per.get(key):
            continue
        per[key] = 1
        p = pew
        if len(real) >= 3 and p == "niska":
            p = "średnia"
        open_ = any(re.search(OPEN, low(h[1])) for h in real)
        out.append({"kod": kod, "nazwa": CATALOG[kod], "sygnal": war, "zdarzenie": zd, "wplyw": wp, "srodki": srod, "pewnosc": p,
                    "uwaga": "W opisie sprawa jest otwarta / nie potwierdzona." if open_ else "",
                    "dowody": [ev(*h, n=280) for h in real[:2]], "liczba_wystapien": len(real), "zrodlo": "treść opisu"})
    return out


def months_from(F: dict) -> float | None:
    if F.get("czas_mies"):
        return float(F["czas_mies"]["wartosc"])
    a, b = F.get("rok_rozpoczecia"), F.get("rok_zakonczenia")
    if a and b and b["wartosc"] >= a["wartosc"]:
        return float((b["wartosc"] - a["wartosc"] + 1) * 12)
    return None


def experience(db: ing.Db | None, codes: list[str], podsektor: str | None, limit: int = 8) -> dict:
    empty = {"przyklady": [], "n_wzorcowych": 0, "n_zdarzen": 0}
    if db is None or not codes:
        return empty
    c = db.c
    try:
        gold = {r[0] for r in c.execute("SELECT DISTINCT projekt FROM decyzje WHERE akcja='Oznaczono jako wzorcowy' AND projekt IS NOT NULL")}
        ok = {r[0] for r in c.execute("SELECT DISTINCT projekt FROM powiazania WHERE status='zatwierdzone'")}
        q = ",".join("?" * len(codes))
        rows = c.execute(f"""SELECT z.projekt, z.kod, z.opis, z.ogloszenie, z.pole, z.cytat, p.nazwa, p.beneficjent, p.wojewodztwo, o.url
                             FROM zdarzenia z JOIN projekty p ON p.id=z.projekt LEFT JOIN ogloszenia o ON o.numer=z.ogloszenie
                             WHERE z.kod IN ({q}) AND z.status NOT LIKE 'odrzuc%'""", codes).fetchall()
    except Exception:
        return empty
    out = []
    for pr, kod, opis, ogl, pole, cyt, nazwa, ben, woj, url in rows:
        try:
            sub = cfg.subsector(nazwa or "")
        except Exception:
            sub = None
        out.append({"projekt": pr, "beneficjent": ben, "nazwa": nazwa, "wojewodztwo": woj, "kod": kod, "opis": opis or "", "ogloszenie": ogl,
                    "pole": pole, "cytat": (cyt or "")[:240], "url": url, "wzorcowy": pr in gold, "zatwierdzony": pr in ok,
                    "ten_sam_podsektor": bool(podsektor and sub == podsektor)})
    out.sort(key=lambda e: (not e["wzorcowy"], not e["zatwierdzony"], not e["ten_sam_podsektor"], e["kod"], e["projekt"]))
    picked, per = [], {}
    for e in out:
        if per.get(e["kod"], 0) < 2 and len(picked) < limit:
            per[e["kod"]] = per.get(e["kod"], 0) + 1
            picked.append(e)
    return {"przyklady": picked, "n_wzorcowych": len(gold), "n_zdarzen": len(out)}


def analyze_file(db: ing.Db, path: Path) -> dict:
    fmt, units, ocr = read_text(path)
    sents = sentences(units)
    if not sents:
        raise RuntimeError(f"Nie udało się odczytać tekstu z {path.name}.")
    F = extract_facts(db, sents, units)
    chk = checklist(sents)
    if F["podsektor"]["wartosc"] != "Sieć wodociągowa":
        for c in chk:
            c["uwaga"] = (f"Checklista z materiału eksperckiego dotyczy sieci wodociągowej; dla podsektora „{F['podsektor']['wartosc']}” "
                          "stosowana odpowiednio (zasilanie ⇢ odbiór ścieków / odbiornik, operator ⇢ eksploatator oczyszczalni).")
    risks = risk_signals(sents)

    koszt = F.get("koszt", {}).get("wartosc", {})
    val = koszt.get("kwota")
    val_b = val * 1.23 if val and koszt.get("rodzaj") == "netto" else val
    mies = months_from(F)
    title = F.get("nazwa", {}).get("wartosc", path.stem)
    full = " ".join(s for _, s in sents)
    req = {"opis": title + ". " + full[:4000], "wartosc": val_b, "czas": mies, "wojewodztwo": F.get("wojewodztwo", {}).get("wartosc"),
           "cechy": {"podsektor": F["podsektor"]["wartosc"], "slowa": bm.words(title + " " + (F.get("miejscowosci", {}).get("wartosc") or "")),
                     "dlugosc_km": F.get("dlugosc_km", {}).get("wartosc")}}
    try:
        bench = bm.analyze(db, req)
    except Exception as e:
        bench = {"ok": False, "blad": str(e)}

    data_risks = []
    if bench.get("ok"):
        st = bench["statystyka"]
        t, k = st["termin"], st["koszt"]
        if t.get("udzial_po_terminie") is not None:
            data_risks.append({"kod": "R06", "nazwa": CATALOG["R06"], "sygnal": f"{t['udzial_po_terminie']}% podobnych umów zakończyło się po terminie (n = {t['n']}; {st['zakres']})",
                               "zdarzenie": "Opóźnienie zakończenia robót", "wplyw": f"typowe opóźnienie: mediana {bm.pl(t['mediana'] or 0)} mies., 75. percentyl {bm.pl(t['p75'] or 0)} mies.",
                               "srodki": "Rezerwa czasowa w harmonogramie, kamienie milowe, klauzula zmiany terminu", "pewnosc": "średnia" if t["udzial_po_terminie"] >= 30 else "niska",
                               "dowody": [], "zrodlo": "BZP: pole 5.3 ogłoszeń o wykonaniu umowy (grupa porównawcza)"})
        if k.get("p75") is not None:
            data_risks.append({"kod": "R07", "nazwa": CATALOG["R07"], "sygnal": f"wzrost kosztu w {k['udzial_wzrost']}% podobnych umów; 75. percentyl zmiany {bm.pl(k['p75'])}% (n = {k['n']})",
                               "zdarzenie": "Roboty dodatkowe / zmiany wynagrodzenia", "wplyw": f"ponad 15% wzrostu w {k['udzial_powyzej_15']}% umów",
                               "srodki": "Rezerwa budżetowa na poziomie 75. percentyla, klauzule zmian z limitem", "pewnosc": "średnia" if (k["udzial_powyzej_15"] or 0) >= 15 else "niska",
                               "dowody": [], "zrodlo": "BZP: pola 4.4 i 5.5 ogłoszeń o wykonaniu umowy (grupa porównawcza)"})
        for fl in bench.get("flagi", []):
            if fl["kod"] == "R06":
                data_risks.append({"kod": "R06", "nazwa": CATALOG["R06"], "sygnal": fl["tekst"], "zdarzenie": "Harmonogram krótszy niż realny czas realizacji",
                                   "wplyw": "Opóźnienie, kary, presja na jakość", "srodki": "Wydłużyć harmonogram do mediany podobnych umów lub uzasadnić krótszy",
                                   "pewnosc": fl["pewnosc"], "dowody": [], "zrodlo": fl["zrodlo"]})

    gap_risks = []
    for c in chk:
        if c["status"] in ("opis nie wspomina", "częściowo"):
            gap_risks.append({"punkt": c["id"], "kody": c["ryzyka"], "opis": f"Checklist {c['id']}: {c['punkt']} – {c['status']}" +
                              (f" (brak: {', '.join(c['brakuje'])})" if c["brakuje"] else ""), "uzupelnienie": c["uzupelnienie"]})

    ai = None
    if llm.config()["enabled"]:
        try:
            ai = llm.invest(sents, [x["kod"] for x in risks if x["pewnosc"] != "informacja"])
        except Exception as e:
            ai = {"blad": f"{type(e).__name__}: {e}"[:300], "model": llm.config()["model"], "ryzyka": [], "fakty": [], "odrzucone": []}

    lvl = {}
    for r in risks:
        if r["pewnosc"] == "informacja":
            continue
        lvl.setdefault(r["kod"], {"punkty": 0, "zrodla": []})
        lvl[r["kod"]]["punkty"] += 2 if r["pewnosc"] == "średnia" else 1
        lvl[r["kod"]]["zrodla"].append("opis: " + r["sygnal"])
    for r in data_risks:
        lvl.setdefault(r["kod"], {"punkty": 0, "zrodla": []})
        lvl[r["kod"]]["punkty"] += 2 if r["pewnosc"] == "średnia" else 1
        lvl[r["kod"]]["zrodla"].append("BZP: " + r["sygnal"])
    for x in (ai or {}).get("ryzyka", []):
        lvl.setdefault(x["kod"], {"punkty": 0, "zrodla": []})
        lvl[x["kod"]]["punkty"] += 1
        lvl[x["kod"]]["zrodla"].append("model: " + x["sygnal"])
    for g in gap_risks:
        for kd in g["kody"]:
            lvl.setdefault(kd, {"punkty": 0, "zrodla": []})
            lvl[kd]["punkty"] += 1
            lvl[kd]["zrodla"].append("luka: checklist " + g["punkt"])
    exp = experience(db, sorted(lvl), F["podsektor"]["wartosc"])
    for e in exp["przyklady"]:
        if e["wzorcowy"] and e["kod"] in lvl and not any(z.startswith("wzorcowy:") for z in lvl[e["kod"]]["zrodla"]):
            lvl[e["kod"]]["punkty"] += 1
            lvl[e["kod"]]["zrodla"].append(f"wzorcowy: {e['projekt']} {e['beneficjent']} ({e['opis'][:80]})")
    matrix = []
    for kd in sorted(lvl):
        p = lvl[kd]["punkty"]
        matrix.append({"kod": kd, "nazwa": CATALOG[kd], "poziom": "wysokie" if p >= 4 else "średnie" if p >= 2 else "niskie", "punkty": p, "podstawy": lvl[kd]["zrodla"]})
    matrix.sort(key=lambda x: -x["punkty"])

    readiness = round(sum(c["punkty"] for c in chk) / len(chk) * 100)
    high = [m for m in matrix if m["poziom"] == "wysokie"]
    mid = [m for m in matrix if m["poziom"] == "średnie"]
    overall = "wysokie" if len(high) >= 2 or (high and readiness < 60) else "średnie" if high or len(mid) >= 2 or readiness < 70 else "niskie"

    needs = []
    for c in chk:
        if c["uzupelnienie"]:
            needs.append({"punkt": c["id"], "czego_brakuje": c["uzupelnienie"],
                          "jak_ogranicza": f"bez tego punkt „{c['punkt'][:70]}…” ma status „{c['status']}” – ryzyka {', '.join(c['ryzyka'])} oceniane z obniżoną pewnością"})
    if not val:
        needs.append({"punkt": "koszt", "czego_brakuje": "szacunkowy koszt inwestycji (z informacją netto/brutto)", "jak_ogranicza": "grupa porównawcza bez zawężenia po wartości; brak oceny budżetu"})
    elif koszt.get("rodzaj", "").startswith("nie podano"):
        needs.append({"punkt": "koszt", "czego_brakuje": "informacja, czy koszt jest netto czy brutto", "jak_ogranicza": "porównanie z wartościami umów z BZP (brutto) obarczone błędem do 23%"})
    if not mies:
        needs.append({"punkt": "harmonogram", "czego_brakuje": "planowany czas realizacji (miesiące lub daty)", "jak_ogranicza": "nie da się porównać terminu z realnym czasem podobnych umów"})
    if "wojewodztwo" not in F:
        needs.append({"punkt": "lokalizacja", "czego_brakuje": "województwo / gmina", "jak_ogranicza": "grupa porównawcza bez zawężenia regionalnego"})

    return {"ok": True, "plik": path.name, "format": fmt, "ocr": ocr, "zdan": len(sents), "czas_analizy": datetime.now().isoformat(timespec="seconds"),
            "podsumowanie": {"nazwa": title, "gotowosc_pct": readiness, "ocena_ryzyka": overall,
                             "najwazniejsze_ryzyka": [f"{m['kod']} {m['nazwa']} – {m['poziom']}" for m in matrix[:4]],
                             "najwazniejsze_uzupelnienia": [n["czego_brakuje"] for n in needs[:4]]},
            "fakty": F, "checklista": chk, "ryzyka_z_opisu": risks, "ryzyka_z_danych_bzp": data_risks, "luki": gap_risks,
            "macierz_ryzyk": matrix, "porownanie_bzp": bench, "doswiadczenia": exp, "uzupelnienia": needs, "ai": ai, "tryb_analizy": llm.status(),
            "metoda": ["Checklista: 9 punktów zbiorczej checklisty z materiału eksperckiego (rozdz. 8); status na podstawie wzorców słów z cytatem i miejscem.",
                       "Rozróżniamy: „ujęte”, „ujęte, sprawa otwarta (brak potwierdzenia)”, „częściowo”, „opis nie wspomina” – brak wzmianki ≠ potwierdzenie, że czegoś nie zrobiono.",
                       "Ryzyka wg katalogu R01–R15: sygnał → możliwe zdarzenie → wpływ → środki zaradcze; pewność obniżona, gdy opiera się tylko na braku informacji.",
                       "Porównanie z BZP: zakończone umowy (ogłoszenia o wykonaniu umowy) z tego samego podsektora, zawężone po wartości i województwie, gdy próba ≥ 30.",
                       "Doświadczenia z bazy: zdarzenia R01–R15 z projektów MFiPR o tych samych kodach; najpierw przykłady oznaczone przez eksperta jako wzorcowe (+1 pkt w macierzy), potem zatwierdzone powiązania i ten sam podsektor.",
                       "Kwoty: wartość umów w BZP jest brutto; koszt netto z opisu przeliczany ×1,23 do porównania.",
                       ("Model językowy: " + llm.status()["model"] + " (" + llm.status()["gdzie"] + ") – tylko propozycje z cytatem sprawdzonym dosłownie w dokumencie; reguły pozostają podstawą."
                        if llm.config()["enabled"] else "Tryb: reguły ekspertów + statystyka z BZP (bez modelu językowego) – wynik deterministyczny, powtarzalny, działa offline."),
                       "Wszystkie wnioski są propozycjami do weryfikacji eksperta."]}


def fmt_zl(v):
    return f"{v:,.0f} zł".replace(",", " ") if v else "—"


def to_markdown(r: dict) -> str:
    P, F = r["podsumowanie"], r["fakty"]
    L = [f"# Analiza nowej inwestycji – {P['nazwa']}", "",
         f"*BidRadar · plik: {r['plik']} ({r['format']}{', OCR' if r['ocr'] else ''}, {r['zdan']} zdań) · {r['czas_analizy']} · tryb: {r.get('tryb_analizy', {}).get('tryb', 'reguły + statystyka')} · wynik do weryfikacji eksperta*", "",
         "## 1. Podsumowanie", "",
         f"- **Gotowość opisu względem checklisty eksperckiej:** {P['gotowosc_pct']}% (9 punktów)",
         f"- **Ocena ryzyka (propozycja):** {P['ocena_ryzyka']}",
         "- **Najważniejsze ryzyka:** " + ("; ".join(P["najwazniejsze_ryzyka"]) or "—"),
         "- **Najważniejsze uzupełnienia:** " + ("; ".join(P["najwazniejsze_uzupelnienia"]) or "—"), "",
         "## 2. Fakty odczytane z opisu", "", "| Cecha | Wartość | Źródło w dokumencie |", "|---|---|---|"]
    names = {"nazwa": "Nazwa", "inwestor": "Inwestor", "nip": "NIP", "operator": "Operator / eksploatator", "wojewodztwo": "Województwo", "powiat": "Powiat",
             "miejscowosci": "Miejscowości", "podsektor": "Podsektor (MFiPR)", "podsektory_dodatkowe": "Podsektory dodatkowe", "rodzaj_prac": "Rodzaj prac",
             "dlugosc_km": "Długość sieci [km]", "srednica_mm": "Średnica [mm]", "przylacza": "Przyłącza [szt.]", "budynki": "Budynki / nieruchomości",
             "mieszkancy": "Mieszkańcy / osoby", "wydajnosc": "Wydajność / zapotrzebowanie", "koszt": "Koszt", "dofinansowanie_pct": "Dofinansowanie [%]",
             "finansowanie": "Źródła finansowania", "czas_mies": "Czas realizacji [mies.]", "lata": "Lata realizacji", "rok_rozpoczecia": "Rok rozpoczęcia",
             "rok_zakonczenia": "Rok zakończenia", "formula": "Formuła realizacji", "wynagrodzenie": "Wynagrodzenie"}
    for k, n in names.items():
        if k not in F:
            continue
        v = F[k]["wartosc"]
        if k == "koszt":
            v = f"{fmt_zl(v['kwota'])} ({v['rodzaj']}; w tekście: „{v['tekst']}”)"
        elif isinstance(v, list):
            v = ", ".join(v)
        src = F[k].get("miejsce", "")
        cyt = F[k].get("cytat", "")
        L.append(f"| {n} | {str(v).replace('|', '/')} | {src}: „{cyt[:160].replace('|', '/')}”" + " |" if cyt else f"| {n} | {v} | {src} |")
    missing = [n for k, n in names.items() if k not in F and k in ("inwestor", "operator", "wojewodztwo", "dlugosc_km", "przylacza", "koszt", "czas_mies", "formula", "finansowanie")]
    if missing:
        L += ["", "Nie odczytano z opisu: " + ", ".join(missing) + "."]
    L += ["", "## 3. Checklista przygotowania inwestycji (materiał ekspercki, rozdz. 8)", "",
          *( [f"*{r['checklista'][0]['uwaga']}*", ""] if r["checklista"] and r["checklista"][0].get("uwaga") else [] ),
          "| # | Punkt | Status | Dowód / czego brakuje |", "|---|---|---|---|"]
    icon = {"ujęte": "✅", "ujęte, sprawa otwarta (brak potwierdzenia)": "🟡", "częściowo": "🟠", "opis nie wspomina": "❌"}
    for c in r["checklista"]:
        d = []
        for e in c["elementy"]:
            if e["dowody"]:
                d.append(f"**{e['element']}** – {e['dowody'][0]['miejsce']}: „{e['dowody'][0]['cytat'][:140]}”" + (f" *(otwarte: „{e['otwarte'][0]['cytat'][:100]}”)*" if e["otwarte"] else ""))
            elif e["status"] == "opis nie wspomina":
                d.append(f"**{e['element']}** – opis nie wspomina")
        L.append(f"| {c['id']} | {c['punkt']} ({c['rozdzial']}) | {icon.get(c['status'], '')} {c['status']} | {'<br>'.join(d).replace('|', '/')} |")
    L += ["", "## 4. Ryzyka (sygnał → zdarzenie → wpływ → środki zaradcze)", "", "### 4.1 Macierz ryzyk", "", "| Kod | Ryzyko | Poziom | Podstawy |", "|---|---|---|---|"]
    for m in r["macierz_ryzyk"]:
        L.append(f"| {m['kod']} | {m['nazwa']} | {m['poziom']} | {'; '.join(m['podstawy'][:4]).replace('|', '/')} |")
    L += ["", "### 4.2 Sygnały w treści opisu", ""]
    for i, x in enumerate([x for x in r["ryzyka_z_opisu"] if x["pewnosc"] != "informacja"], 1):
        L += [f"**{i}. {x['kod']} {x['nazwa']}** · pewność: {x['pewnosc']}" + (f" · {x['uwaga']}" if x["uwaga"] else ""),
              f"- Sygnał: {x['sygnal']}", f"- Możliwe zdarzenie: {x['zdarzenie']}", f"- Możliwy wpływ: {x['wplyw']}", f"- Środki zaradcze: {x['srodki']}"]
        for d in x["dowody"]:
            L.append(f"- Dowód ({d['miejsce']}): „{d['cytat']}”")
        L.append("")
    info = [x for x in r["ryzyka_z_opisu"] if x["pewnosc"] == "informacja"]
    if info:
        L += ["Sygnały wykluczone przez opis (informacja):", ""] + [f"- {x['kod']}: {x['sygnal']} – „{x['dowody'][0]['cytat'][:160]}” ({x['dowody'][0]['miejsce']})" for x in info] + [""]
    L += ["### 4.3 Sygnały z danych BZP (grupa porównawcza)", ""]
    for x in r["ryzyka_z_danych_bzp"] or []:
        L += [f"- **{x['kod']} {x['nazwa']}** ({x['pewnosc']}): {x['sygnal']}. Wpływ: {x['wplyw']}. Środki: {x['srodki']}. Źródło: {x['zrodlo']}."]
    if not r["ryzyka_z_danych_bzp"]:
        L.append("- brak (grupa porównawcza niedostępna)")
    ai = r.get("ai")
    if ai:
        L += ["", f"### 4.4 Propozycje modelu językowego ({ai.get('model', '')}, cytaty zweryfikowane)", ""]
        if ai.get("blad"):
            L.append(f"- Model niedostępny ({ai['blad']}) – analiza oparta wyłącznie na regułach.")
        else:
            if ai.get("streszczenie"):
                L += [f"> {ai['streszczenie']}", ""]
            for x in ai["ryzyka"]:
                L += [f"- **{x['kod']}** ({x['pewnosc']}): {x['sygnal']} → {x['zdarzenie']} → {x['wplyw']}. Środki: {x['srodki']}. "
                      f"Dowód ({x['dowody'][0]['miejsce']}): „{x['dowody'][0]['cytat']}”"]
            if ai.get("fakty"):
                L += ["", "Fakty wskazane przez model: " + "; ".join(f"{f['cecha']}: {f['wartosc']} ({f['miejsce']})" for f in ai["fakty"])]
            L += ["", f"Odrzucone przez weryfikację cytatów: {len(ai.get('odrzucone', []))} (cytat nie występował dosłownie w dokumencie albo kod spoza katalogu)."]
    L += ["", "### 4.5 Luki w opisie (brak informacji → obniżona pewność)", ""] + [f"- {g['opis']} → ryzyka {', '.join(g['kody'])}" for g in r["luki"]] + [""]
    b = r["porownanie_bzp"]
    L += ["## 5. Porównanie z zakończonymi umowami z BZP", ""]
    if b.get("ok"):
        st = b["statystyka"]
        k, t, cz = st["koszt"], st["termin"], st["czas_realizacji"]
        L += [f"Grupa porównawcza: **{st['n_umow']} umów** ({st['zakres']}) z {st['n_sektor']} ogłoszeń o wykonaniu umowy w kopii BZP.", "",
              "| Wskaźnik | Wartość | n |", "|---|---|---|",
              f"| Zmiana kosztu (wypłata vs umowa): mediana / 75. perc. / 90. perc. | {bm.pl(k['mediana'])}% / {bm.pl(k['p75'])}% / {bm.pl(k['p90'])}% | {k['n']} |",
              f"| Umowy ze wzrostem kosztu / powyżej +15% | {k['udzial_wzrost']}% / {k['udzial_powyzej_15']}% | {k['n']} |",
              f"| Umowy zakończone po terminie | {t['udzial_po_terminie']}% | {t['n']} |",
              f"| Opóźnienie (wśród opóźnionych): mediana / 75. perc. [mies.] | {bm.pl(t['mediana'] or 0)} / {bm.pl(t['p75'] or 0)} | {t['n_opoznien']} |",
              f"| Czas realizacji: mediana / 75. perc. [mies.] | {bm.pl(cz['mediana'] or 0)} / {bm.pl(cz['p75'] or 0)} | {cz['n']} |",
              f"| Wykonanie nienależyte | {bm.pl(st['nienalezyte_pct'])}% | |", ""]
        if F.get("koszt") and k.get("p75") is not None:
            v = F["koszt"]["wartosc"]["kwota"] * (1.23 if F["koszt"]["wartosc"]["rodzaj"] == "netto" else 1)
            L += [f"Dla kosztu {fmt_zl(v)} (brutto) 75. percentyl zmiany oznacza ok. **{fmt_zl(v * max(k['p75'], 0) / 100)}** możliwego wzrostu.", ""]
        L += ["Najbardziej podobne zakończone umowy:", "", "| Podob. | Zamawiający | Przedmiot | Woj. | Wartość | Zmiana kosztu | W terminie | Ogłoszenie |", "|---|---|---|---|---|---|---|---|"]
        for s in b["podobne"]:
            L.append(f"| {s['podobienstwo']} | {s['zamawiajacy']} | {(s['przedmiot'] or '')[:90].replace('|', '/')} | {s['wojewodztwo'] or '—'} | {bm.fmt_mln(s['wartosc'])} | "
                     f"{bm.pl(s['zmiana_pct'])}% | {'tak' if s['w_terminie'] else 'nie' if s['w_terminie'] == 0 else '—'} | [{s['numer']}]({s['url']}) |")
        L += exp_md(r.get("doswiadczenia"))
        L += ["", "## 6. Rekomendacje i proponowane zapisy umowne", ""] + [f"- {x}" for x in b.get("rekomendacje", [])] + [""] + [f"- {x}" for x in b.get("zapisy_umowne", [])]
    else:
        L += [f"Porównanie niedostępne: {b.get('blad', '')}"] + exp_md(r.get("doswiadczenia")) + ["", "## 6. Rekomendacje", ""]
    L += [f"- {x['srodki']} ({x['kod']})" for x in r["ryzyka_z_opisu"] if x["pewnosc"] == "średnia"]
    L += ["", "## 7. Potrzebne uzupełnienia (czego brakuje do silniejszej oceny)", "", "| Obszar | Czego brakuje | Jak ogranicza analizę |", "|---|---|---|"]
    for n in r["uzupelnienia"]:
        L.append(f"| {n['punkt']} | {n['czego_brakuje']} | {n['jak_ogranicza']} |")
    L += ["", "## 8. Metoda i ograniczenia", ""] + [f"- {m}" for m in r["metoda"]] + [""]
    return "\n".join(L)


def exp_md(x: dict | None) -> list[str]:
    if not x or not x.get("przyklady"):
        return []
    L = ["", "### 5.1 Doświadczenia z bazy projektów MFiPR (te same kody ryzyka)", "",
         f"Zdarzeń o tych kodach w bazie: {x['n_zdarzen']}; projektów wzorcowych: {x['n_wzorcowych']}. Wzorcowe i zatwierdzone przez eksperta są pierwsze.", "",
         "| Kod | Projekt | Co się stało | Źródło |", "|---|---|---|---|"]
    for e in x["przyklady"]:
        tag = "**wzorcowy** · " if e["wzorcowy"] else "zatwierdzony · " if e["zatwierdzony"] else ""
        src = f"[{e['ogloszenie']}]({e['url']})" if e.get("url") else (e.get("ogloszenie") or "—")
        L.append(f"| {e['kod']} | {tag}{e['projekt']} {e['beneficjent'] or ''} | {e['opis'][:110].replace('|', '/')} | {src}{', pole ' + e['pole'] if e.get('pole') else ''} |")
    return L


def slug(s: str) -> str:
    s = re.sub(r"[^\w]+", "_", s.lower()).strip("_")
    return s[:60] or "inwestycja"


def save(r: dict, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    base = out / f"analiza_nowej_inwestycji_{slug(Path(r['plik']).stem)}"
    md, js = base.with_suffix(".md"), base.with_suffix(".json")
    md.write_text(to_markdown(r), encoding="utf-8")
    js.write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    h = report.save_html(md, subtitle="Analiza nowej inwestycji")
    dx = rm_docx.save_docx(md, "Analiza nowej inwestycji – " + str(r.get("podsumowanie", {}).get("nazwa", ""))[:80])
    return {"md": str(md), "json": str(js), "html": str(h), "docx": str(dx)}


def main():
    ap = argparse.ArgumentParser(description="BidRadar – analiza opisu nowej inwestycji")
    ap.add_argument("plik", type=Path)
    ap.add_argument("--db", type=Path, default=Path(os.environ.get("RM_DB", "bidradar.db")))
    ap.add_argument("--out", type=Path, default=Path("wyniki"))
    a = ap.parse_args()
    db = ing.Db(a.db)
    db.c.executescript(bm.SCHEMA)
    r = analyze_file(db, a.plik)
    p = save(r, a.out)
    print(f"Gotowe: {p['md']}\n        {p['json']}")


if __name__ == "__main__":
    main()
