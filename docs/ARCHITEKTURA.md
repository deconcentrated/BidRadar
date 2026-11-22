# BidRadar – architektura

## Zasady

1. **Zero zależności.** Sama biblioteka standardowa Pythona 3.10+ i jeden plik SQLite. Uruchomienie nie wymaga `pip install`, Dockera ani internetu.
2. **Dane źródłowe → obliczenia → wnioski modelu.** Trzy warstwy są rozdzielone w danych, w API i w interfejsie (kolory i podpisy). Wniosek modelu ma zawsze status „propozycja”.
3. **Każdy rekord ma pochodzenie.** Numer ogłoszenia, URL, pole formularza lub strona dokumentu, cytat, data pobrania, status i historia decyzji.
4. **Konfiguracja zamiast kodu.** Sektory, wagi, źródła i katalog ryzyk zmienia się w plikach `konfiguracja/` albo w panelu eksperta.
5. **Awaria źródła ≠ awaria aplikacji.** Brak TED, internetu albo modelu językowego obniża zakres wyniku, ale nie przerywa pracy.

## Moduły

```
                     ┌──────────────── przeglądarka ───────────────┐
                     │ landing.html  login.html  bidradar_ui.html  │  (samodzielne pliki, offline)
                     └───────────────────────┬─────────────────────┘
                                             │ HTTP/JSON (ciasteczko sesji)
┌────────────────────────────────────────────┴───────────────────────────────────────────┐
│ server.py   trasy, sesje i role (rm_auth), CSRF, limity, monitoring w tle, eksporty      │
└──┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────┬───────────┘
   │              │              │              │              │              │
rm_ingest     rm_ted        rm_docs       rm_benchmark   rm_invest      rm_record
arkusz+BZP    TED API v3    DOCX/PDF/OCR  grupa porów.   opis nowej     rekord projektu
łączenie      łączenie      fragmenty     ~27 tys. umów  inwestycji     MD/JSON/CSV/HTML
zdarzenia     po NIP/nazwie weryfikacja   przedziały     checklista     │
z reguł                     różnice       podobne umowy  macierz R01–15 │
   │              │              │              │              │       rm_report (HTML/PDF)
   └──────────────┴──────────────┴──────┬───────┴──────────────┘
                                        │          rm_config (konfiguracja/ + ustawienia w bazie)
                                 bidradar.db (SQLite, WAL)          rm_llm (opcjonalnie, z bezpiecznikami)
```

## Model danych (SQLite)

| Tabela | Encja | Klucze i powiązania |
|---|---|---|
| `projekty` | Projekt z arkusza MFiPR | `id` (RM-2026-001), NIP, identyfikator publiczny (BZP/KPO) |
| `powiazania` | Projekt ↔ postępowanie | `projekt`, `ocid`, metoda, pewność 0–1, status, podstawa |
| `postepowania` | Postępowanie | `ocid`, zamawiający, NIP, CPV, liczba ofert, braki danych |
| `ogloszenia` | Ogłoszenie BZP | `numer`, typ, `ocid`, data, URL |
| `umowy` | Umowa / część | `ocid`, `czesc`, wartość brutto, wypłacono, terminy, należyte wykonanie |
| `zmiany` | Zmiana umowy | `ocid`, ogłoszenie, podstawa (art. 455 Pzp), przyczyna |
| `zdarzenia` | Zdarzenie ryzyka | projekt, kod R01–R15, ogłoszenie, **pole**, **cytat**, status |
| `dokumenty`, `fragmenty`, `weryfikacja`, `roznice` | Dokument i jego analiza | powiązanie z projektem, fragmenty z miejscem i cytatem, zgodność z BZP, zmiany wobec wcześniejszego stanu |
| `ted` | Ogłoszenie TED | numer publikacji, zamawiający, CPV, wartość, powiązanie z projektem i status |
| `umowy_sektor` | Grupa porównawcza | ogłoszenia o wykonaniu umowy z całego BZP: zmiana kosztu, opóźnienie, podsektor |
| `analizy` | Analiza nowej inwestycji | opis wejściowy i pełny wynik |
| `alerty` | Monitoring projektów w toku | nowe ogłoszenie, istotność, status przejrzenia |
| `decyzje` | Historia decyzji | czas, akcja, czego dotyczy, **ekspert**, komentarz |
| `ustawienia` | Ustawienia eksperta | wagi podobieństwa, katalog ryzyk – z autorem i czasem |
| `uzytkownicy`, `sesje`, `logowania` | Konta | scrypt z solą, skrót tokenu sesji, dziennik logowań |
| `log` | Dziennik zapytań | każde zapytanie do BZP i TED z wynikiem |

Wartość projektu ≠ wartość umowy. Kwoty netto (szacunek) i brutto (oferty, umowy) nie są porównywane ze sobą.

## Przepływ: import i łączenie

1. Arkusz → `projekty`.
2. Numer BZP (z normalizacją `2022/BZP 00058537/01 ↔ 2022/BZP 00058537`) → ogłoszenie → OCID → wszystkie ogłoszenia zamawiającego z tym OCID. API BZP nie filtruje po OCID, więc filtrujemy po stronie klienta.
3. Kontrola NIP. Pewność 100% tylko przy zgodnym numerze **i** NIP.
4. Brak ogłoszenia o zamówieniu → odtworzenie z odwołań w polach 3.2.1 i 4.5 późniejszych ogłoszeń → do eksperta.
5. Brak numeru (KPO) → dopasowanie rozmyte: NIP, nazwa, miejscowość, CPV, wartość, daty → zawsze do eksperta.
6. Zdarzenia z reguł (bez AI): pole 5.3 = Nie, 5.5 vs 4.4, 5.6, art. 455 Pzp, unieważnienia, liczba ofert.
7. TED: zapytanie po CPV robót wod-kan i kraju → łączenie po NIP (95%) albo nazwie i miejscowości (70%, do eksperta).
8. Decyzje eksperta są zapisane osobno i stosowane ponownie po każdym imporcie.

## Skalowanie i rozwój

- **Więcej ekspertów:** SQLite w trybie WAL, osobne połączenie na wątek, import w tle bez blokowania odczytów. Wystarcza dla zespołu pracującego na jednej instancji.
- **Więcej instancji / SSO:** te same tabele przenoszą się 1:1 do PostgreSQL. Logowanie przez system ministerstwa podpina się na reverse proxy (np. oauth2-proxy) z nagłówkiem użytkownika.
- **Nowy sektor:** nowy plik reguł podsektorów w `konfiguracja/` i własne kody CPV w `zrodla.json`.
- **Nowe źródło:** moduł na wzór `rm_ted.py` z funkcjami `run()` (pobranie i powiązanie z projektami), `stats()` i `for_project()`, zapisujący każde zapytanie w `log`.
- **Produkcyjne e-Zamówienia:** zmiana `RM_BZP_URL`. Klient trzyma się zapowiedzianych od 11.01.2027 ograniczeń API (PageSize ≤ 100).
