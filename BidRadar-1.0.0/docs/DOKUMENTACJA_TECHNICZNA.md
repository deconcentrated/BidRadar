# BidRadar – mapa ryzyk inwestycji wodno-kanalizacyjnych


Ekspert wskazuje miejsce na mapie albo opisuje projekt i w kilka minut dostaje podobne zakończone inwestycje, realne przedziały opóźnień i wzrostu kosztów, czerwone flagi i rekomendacje. Każda liczba prowadzi do źródła, a każdy wniosek AI zatwierdza człowiek.

- **Tylko Python 3.10+**, bez instalowania bibliotek. Baza danych to plik SQLite.
- **Działa offline**: mapa, czcionki i granice województw są wbudowane w strony.
- **Wynik deterministyczny**: reguły ekspertów i statystyka z BZP. Model językowy jest opcjonalny i tylko proponuje.

| Adres | Co tam jest |
|---|---|
| `/` | strona produktu (landing) |
| `/app` | aplikacja: mapa, projekty, dokumenty, panel eksperta, źródła danych |
| `/login` | logowanie eksperta (potrzebne tylko do zapisywania decyzji) |
| `/healthz` | stan serwera (dla monitoringu i proxy) |

---

## Start w minutę

```bash
bash uruchom.sh                 # na tym komputerze  → http://127.0.0.1:8000
bash uruchom.sh --siec          # z innych komputerów (VM dla jury) → http://ADRES_VM:8000
bash uruchom.sh --bez-logowania # jedna osoba, bez kont
```

Skrypt sam znajduje arkusz MFiPR i kopię BZP, uruchamia kopię, sprawdza narzędzia do PDF i OCR, a na końcu startuje aplikację. Przy pierwszym starcie importuje projekty z BZP (ok. 6 s) i w tle buduje grupę porównawczą z ok. 27 tys. umów (1–3 min).

**Konta.** Pierwszy start tworzy dwa konta: `ekspert` (zatwierdzający) i `analityk`. Hasło pojawia się w konsoli i w pliku `hasla_startowe.txt` obok bazy. Plik jest w `.gitignore`. Na VM dla jury ustaw stałe hasło: `RM_DEMO_PASSWORD=… bash uruchom.sh --siec`. Przeglądanie, eksporty i analiza nowej inwestycji działają bez logowania.

PDF i skany wymagają narzędzi systemowych (jednorazowo): `sudo apt install poppler-utils tesseract-ocr tesseract-ocr-pol`. Dla starych plików `.doc`: `antiword` albo LibreOffice.

## Test jury: dwa pliki, bez zmian w kodzie

```bash
bash test.sh /ścieżka/do/rekordu_projektu  /ścieżka/do/opisu_inwestycji
```

Kolejność i formaty plików są dowolne. BidRadar sam rozpoznaje, który plik jest rekordem, a który opisem. Wyniki trafiają do `wyniki_testu/` jako raport `.md`, dokument Word `.docx`, raport do druku `.html` (zapis do PDF z przeglądarki), dane `.json` i wiersz `.csv`.

Próba generalna: `bash test.sh przyklady_testu/przyklad_rekord.xlsx przyklady_testu/przyklad_opis_inwestycji.docx`.

Pomiar trafności wyszukiwania w BZP: `python3 tools/porownanie_llm.py --tylko-nasze`.

Raport liczb do prezentacji: `python3 bidradar_eval.py` → `raport_testu.md`. Trafność prognoz ryzyka (grupa porównawcza vs ML, kalibracja, pokrycie przedziałów): `python3 tools/ocena_predykcji.py` → [`raport_predykcji.md`](../raport_predykcji.md) (przebieg na świeżej bazie; Wasza baza z decyzjami zostaje nietknięta).

---

## Gdzie co jest: wymagania i kryteria oceny

### Testy jury

| Test | Co zrobi juror | Gdzie to jest w BidRadar |
|---|---|---|
| Działanie na żywo | Uruchomi aplikację na zbiorze testowym | `bash uruchom.sh --siec` albo usługa systemd / Docker (sekcja *Wdrożenie*) |
| Integracja | Pobierze dane z publicznych źródeł | **Źródła danych → Odśwież wszystkie**: BZP i TED, dziennik każdego zapytania, bez ręcznego wpisywania |
| Łączenie | Porówna powiązania z zestawem referencyjnym | **Źródła danych → Pobierz raport połączeń (CSV)**: BZP i TED, metoda, pewność, podstawa i ostatnia decyzja eksperta (`/api/export/links`) |
| Kompletność przypadku | Sprawdzi projekt: zamówienie, wynik, zmiany, wykonanie | **Karta projektu**: oś czasu ogłoszeń BZP i TED, kwoty plan i wykonanie, zdarzenia ryzyka |
| Nowa inwestycja | Poda opis lub dokumenty | **Mapa → Analizuj nową inwestycję**: podobne umowy, przedziały, flagi, rekomendacje; raport PDF/HTML |
| Źródło | Sprawdzi, czy źródło potwierdza wniosek | każda wartość ma link do ogłoszenia, numer pola i cytat |
| Praca eksperta | Zaakceptuje, poprawi lub odrzuci dopasowanie | **Panel eksperta**: Zatwierdź / Popraw / Odrzuć (z komentarzem) / Wzorcowy, **Zatwierdź pewne połączenia** (zbiorczo, ≥ 90%), historia z nazwiskiem; wzorcowe projekty idą pierwsze w sekcji *Doświadczenia z bazy* każdej analizy nowej inwestycji |
| Założenia | Oceni uniwersalność i skalowalność | `konfiguracja/` (sektory, wagi, źródła bez kodu), [ARCHITEKTURA.md](ARCHITEKTURA.md) |
| Fakultatywne | Monitorowanie projektów w toku | **Źródła danych → Sprawdź nowe ogłoszenia** (co 6 h automatycznie) i **Pokaż działanie alertu** |

### Wymagania funkcjonalne (sekcja 5 specyfikacji)

| Wymaganie | Realizacja |
|---|---|
| 5.1 Import i automatyczne zasilanie | arkusz MFiPR, rekordy XLSX/CSV/JSON; dokumenty DOCX, DOC, PDF (OCR), ODT, RTF, HTML, TXT; BZP (kopia lub produkcja – jedna linia w `.env`); **TED** (publiczne API v3, bez klucza) – `rm_ingest.py`, `rm_ted.py` |
| 5.1 Ponowna analiza po nowym dokumencie | karta dokumentu: **Zmiany względem wcześniejszych danych** – fakty potwierdzone / zmienione / nowe, ryzyka potwierdzone / nowe / zamknięte (`rm_docs.diff`) |
| 5.1 Zakaz ręcznych wpisów | wszystkie rekordy powstają z importu; ręcznie wpisuje się tylko decyzje eksperta |
| 5.2 Łączenie rekordów | numer BZP z normalizacją → OCID → wszystkie ogłoszenia; NIP; odwołania z pól 3.2.1 i 4.5 (przypadek Wolina); dopasowanie rozmyte z pewnością i powodami; TED po NIP i nazwie |
| 5.2 Raport połączeń | poprawne / niepewne / odrzucone, eksport CSV |
| 5.3 Model danych | projekt → postępowanie → część → ogłoszenie → umowa → zmiana → zdarzenie → dokument; netto i brutto nie są porównywane |
| 5.4 Identyfikowalność | źródło, link, data pobrania, pole/strona, cytat, status i historia decyzji przy każdym rekordzie |
| 5.5 Ekstrakcja zdarzeń | reguły bez AI (pola 5.3, 5.5 vs 4.4, 5.6, art. 455 Pzp, unieważnienia, liczba ofert); LLM tylko klasyfikuje przyczynę do R01–R15, z cytatem sprawdzanym dosłownie |
| 3. Skutki, przyczyny, działania | przy każdym zdarzeniu: skutek w dniach (termin planowany → wykonanie) i zł (wypłacono − wartość umowy) z BZP, przyczyna z pola 5.3 ogłoszenia o zmianie, działanie zaradcze (zmiana umowy) i jego skuteczność (czy po zmianie umowa wykonana w terminie) – automatycznie ze źródłem; ekspert poprawia i zatwierdza |
| 5.6 Panel eksperta | kolejka od najmniej pewnych; Zatwierdź / Popraw / Odrzuć (**komentarz obowiązkowy**) / **Wzorcowy** / zatwierdzanie zbiorcze; historia: kto, kiedy, co; edycja katalogu ryzyk, **wag podobieństwa** i **reguł podsektorów** bez kodu; w karcie projektu: **poprawka zdarzenia** (kategoria, przyczyna, skutek w dniach i zł, działanie zaradcze, skuteczność, status), **poprawka rekordu** (NIP, numer BZP, nazwa…) i **zmiana powiązania** (numer BZP albo OCID); poprawki są w tabeli `korekty` i przetrwają ponowny import |
| 5.7 Nowa inwestycja | wejście: punkt, obszar, współrzędne, gmina, opis lub plik; wyjście: grupa porównawcza z uzasadnieniem, mediana i percentyle z liczebnością próby, flagi, rekomendacje i zapisy umowne, podział dane / obliczenia / wnioski |
| 5.8 Eksport | tabele CSV i JSON; raporty **HTML gotowe do zapisu jako PDF**; **pełna baza jako paczka JSON** (`/api/export/db`) |
| 5.9 Monitorowanie (SHOULD) | wątek co `RM_MONITOR_H` godzin; nowe ogłoszenie o zmianie umowy (np. art. 455 ust. 1 pkt 4) trafia na górę kolejki eksperta jako alert |

### Kryteria oceny

| Kryterium | Waga | Co za to odpowiada |
|---|---|---|
| Pozyskiwanie, łączenie i organizacja danych | 20% | BZP + TED automatycznie, łączenie po OCID / NIP / odwołaniach, raport połączeń, dziennik zapytań |
| Analiza dokumentów, zdarzenia, wiarygodność | 20% | DOCX/PDF/OCR, fragmenty R01–R15 z cytatem, weryfikacja krzyżowa z BZP, ponowna analiza po nowym dokumencie |
| Analiza planowanej inwestycji i rekomendacje | 25% | grupa porównawcza z ~27 tys. umów, przedziały z liczebnością, podobne umowy z linkami, checklista ekspercka, raport PDF |
| Kontrola ekspercka, edytowalność, modułowość | 20% | logowanie i role, historia z nazwiskiem, komentarz przy odrzuceniu, wzorcowe przykłady, katalog i wagi w aplikacji, `konfiguracja/` |
| UX/UI, jakość techniczna, potencjał rozwoju | 15% | landing, spójny styl (WCAG AA), testy automatyczne i CI, Docker, systemd, Caddy |

---

## Logowanie i role: bez osobnej bazy danych

Konta, sesje i dziennik logowań leżą w **tym samym pliku SQLite** co dane (`rm_auth.py`). SQLite w trybie WAL bez trudu obsługuje zespół ekspertów: odczyty nie czekają na zapisy, a import z BZP działa w tle. Osobny serwer bazy (np. PostgreSQL) przyda się dopiero przy kilku instancjach aplikacji naraz albo przy logowaniu przez system ministerstwa (SSO). SSO i tak podpina się na poziomie reverse proxy, a nie bazy.

| Rola | Może |
|---|---|
| bez logowania | przeglądać mapę, projekty, karty, źródła, kolejkę, historię; eksportować; analizować nową inwestycję |
| `analityk` | jak wyżej oraz wgrywać dokumenty i rekordy |
| `zatwierdzajacy` | wszystko: decyzje w kolejce, katalog ryzyk, wagi, import z BZP i TED, monitoring |

Zabezpieczenia:
- hasła przez scrypt z solą, a w bazie tylko skrót tokenu sesji;
- ciasteczko `HttpOnly` i `SameSite=Strict`, do tego kontrola `Origin` przy każdym zapisie (CSRF);
- limit nieudanych logowań i limit rozmiaru żądania;
- nagłówki bezpieczeństwa, a błędy bez szczegółów technicznych (szczegóły trafiają do konsoli).

```bash
python3 rm_auth.py dodaj jan.kowalski --rola zatwierdzajacy --nazwa "Jan Kowalski"
python3 rm_auth.py lista
python3 rm_auth.py haslo ekspert
python3 rm_auth.py usun jan.kowalski        # wyłącza konto, historia decyzji zostaje
```

## Konfiguracja bez zmian w kodzie

| Plik / miejsce | Co zmienia |
|---|---|
| `konfiguracja/podsektory.json` | reguły podsektorów; nowy sektor (np. drogi) to nowy plik w tym formacie; grupa porównawcza przebuduje się sama |
| `konfiguracja/wagi.json` + **Panel eksperta → Katalog ryzyk → Wagi** | wagi podobieństwa przy doborze podobnych umów |
| `konfiguracja/zrodla.json` | BZP, TED (kody CPV, kraj, data od), częstotliwość monitoringu |
| **Panel eksperta → Katalog ryzyk** | nazwy i włączenie kategorii R01–R15, nowe kategorie |
| `.env` (wzór: `config.example.env`) | adresy, port, logowanie, model językowy |

## Wdrożenie na VM

**Najprościej:** `RM_DEMO_PASSWORD=wybrane-haslo bash uruchom.sh --siec`, a potem otwórz port 8000 w zaporze.

**Jako usługa z HTTPS** (usługi systemd i Caddy z automatycznym certyfikatem):
```bash
sudo CADDY=1 bash deploy/instaluj.sh 8000 twoja-domena.pl
journalctl -u bidradar -f
```

**Docker:** `docker compose up -d` (arkusz w `./paczka`, kopia BZP na hoście, dane w `./dane`).

## Plik konfiguracyjny `.env` (wzór: `config.example.env`)

| Klucz | Domyślnie | Znaczenie |
|---|---|---|
| `RM_HOST` / `RM_PORT` | `127.0.0.1` / `8000` | adres serwera; `0.0.0.0` = dostęp z sieci |
| `RM_BZP_URL` | `http://127.0.0.1:8010` | API BZP: kopia lokalna albo produkcja e-Zamówień |
| `RM_DB` / `RM_OUT` | `bidradar.db` / `out` | baza SQLite i folder eksportów |
| `RM_AUTH` | `on` | `off` = praca bez kont (jedna osoba) |
| `RM_SESSION_H` | `12` | ważność sesji w godzinach |
| `RM_MONITOR_H` | `6` | co ile godzin sprawdzać nowe ogłoszenia; `0` = wyłączone |
| `RM_MAX_MB` | `40` | maksymalny rozmiar wgrywanego pliku |
| `RM_DEMO_PASSWORD` | losowe | stałe hasło kont startowych (np. na pokaz) |
| `RM_SECURE_COOKIE` | – | `1` = ciasteczko tylko przez HTTPS |
| `RM_OFFLINE` | – | `1` = bez zapytań do internetu (TED, geokodowanie) |
| `LLM_PROVIDER` | puste = wyłączony | `mistral`, `openai` (każde API zgodne z OpenAI, np. Gemini), `anthropic`, `ollama` |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | – | adres API, nazwa modelu, klucz |
| `LLM_MODEL_ZAPASOWY` | – | model używany, gdy główny jest przeciążony |
| `MISTRAL_API_KEY` | – | skrót dla Mistrala (wystarczy sam klucz) |
| `RM_LLM_MASKA` | auto | `1` = zawsze maskuj dane osobowe, `0` = nigdy; domyślnie u dostawców spoza UE |
| `LLM_TIMEOUT` / `LLM_MYSLENIE` | – | czas oczekiwania na model; poziom „myślenia” modeli Gemini |

## Model językowy (opcjonalny)

Model włączasz w `.env`: lokalny Bielik przez Ollama, Mistral (UE) albo Claude. Dla Mistrala wystarczą dwie linie:

```bash
LLM_PROVIDER=mistral
MISTRAL_API_KEY=twój_klucz          # klucz z console.mistral.ai; model domyślnie mistral-small-latest
```

Sprawdzenie połączenia: `python3 rm_llm.py --test`. Mistral działa w trybie JSON i ze stałym `random_seed`, więc te same dane dają tę samą odpowiedź. Model:
- tylko dokłada propozycje w analizie nowej inwestycji i wyjaśnieniu ryzyka;
- każdy jego cytat jest sprawdzany dosłownie w dokumencie, a propozycje bez potwierdzonego cytatu są odrzucane i liczone w raporcie;
- w razie błędu nie przerywa analizy;
- u dostawcy spoza UE (np. Claude, OpenAI) nie dostaje danych osobowych: PESEL, NIP, REGON, e-maile, telefony i numery rachunków są zamieniane na znaczniki `[NIP_1]` przed wysłaniem i przywracane w odpowiedzi, więc cytaty nadal sprawdzamy dosłownie. Wymuszenie dla każdego dostawcy: `RM_LLM_MASKA=1`, wyłączenie: `RM_LLM_MASKA=0`.

Test połączenia z aplikacji: **Źródła danych → Sprawdź połączenie z modelem** (`POST /api/ai/test`).

## API

Pełna lista adresów z rolami: **`/api`** (strona) i **`/api/openapi.json`** (OpenAPI 3.0 – Swagger UI, Postman, generatory klientów).

Nowe w v33: `POST /api/decision/batch` – zatwierdzenie zbiorcze połączeń z kolejki o pewności ≥ `min_pewnosc` (domyślnie 0,9; `"podglad": true` tylko pokazuje listę), każde zapisane w historii osobno; `GET /api/analyses` – historia analiz nowych inwestycji (`?id=` pełny wynik); eksport rekordów i analiz także do **.docx**.

Odczyt bez logowania: `GET /api/projects /api/queue /api/sources /api/decisions /api/docs /api/settings /api/monitor /api/ted /api/me`,
eksport: `GET /api/export/links?fmt=csv|json` (raport połączeń), `/api/export/record?id=RM-2026-001&fmt=md|json|csv|html|docx`, `/api/export/records?fmt=…[&ids=…]`, `/api/export/db`.
Analiza: `POST /api/analyze /api/test/invest /api/test/invest-text /api/explain`.
Zmiany (po zalogowaniu): `POST /api/docs/upload /api/test/record` (analityk), `POST /api/decision /api/refresh /api/settings /api/monitor/run /api/ted/refresh` (zatwierdzający), `POST /api/login /api/logout`.

Zgodne z zapowiedzianymi zmianami API BZP od 11.01.2027 (PageSize ≤ 100, tylko znane parametry). API BZP nie filtruje po NIP ani OCID, więc klient filtruje po swojej stronie.

## Testy i jakość

```bash
python3 -m unittest discover -s tests -v     # 34 testy: logowanie, role, CSRF, TED, dokumenty, monitoring, eksporty (.docx), zatwierdzanie zbiorcze, OpenAPI, maskowanie danych, wzorcowe przykłady
python3 tools/build_pages.py                 # po zmianie szablonów w tools/ (landing, logowanie, dodatek do aplikacji)
```

Testy nie potrzebują kopii BZP ani internetu. TED jest testowany na lokalnym serwerze o tym samym formacie odpowiedzi (`tests/fake_ted.py`). GitHub Actions uruchamia je przy każdym pushu (Python 3.10 i 3.12).

## Uczciwie o ograniczeniach

- **Lokalizacja:** BZP podaje adres zamawiającego, a nie placu budowy. Przy pinezce opisujemy, skąd pochodzi położenie: siedziba zamawiającego z OpenStreetMap albo przybliżenie do województwa (bez internetu).
- **Projekty KPO** nie mają numeru BZP, więc ich dopasowanie jest zawsze propozycją do decyzji eksperta.
- **Brak w kopii BZP ≠ brak publikacji.** Takie przypadki są oznaczone i trafiają do eksperta.
- **TED** obejmuje zamówienia powyżej progów unijnych, więc dla małych inwestycji gminnych często nic nie znajdzie. Bez internetu źródło dostaje status „brak odpowiedzi”, a dane z BZP zostają nienaruszone.
- **„Pokaż działanie alertu”** to jawna symulacja: usuwa z bazy ostatnie ogłoszenie o zmianie umowy, żeby monitoring odkrył je ponownie. Na statycznej kopii BZP nowe ogłoszenia się nie pojawiają.

## Struktura repozytorium

```
server.py            serwer HTTP, logowanie, API, monitoring
rm_ingest.py         import arkusza i BZP, łączenie, zdarzenia z reguł
rm_ted.py            konektor TED (drugie źródło)
rm_benchmark.py      grupa porównawcza i analiza nowej inwestycji
rm_invest.py         analiza opisu inwestycji z pliku (fakty z cytatem, checklista, macierz ryzyk)
rm_docs.py           dokumenty: odczyt, OCR, fragmenty R01–R15, weryfikacja z BZP, ponowna analiza
rm_record.py         rekord projektu: eksport MD / JSON / CSV / HTML
rm_report.py         raport HTML gotowy do druku i PDF
rm_auth.py           konta, role, sesje
rm_config.py         konfiguracja bez kodu (pliki + ustawienia eksperta w bazie)
rm_llm.py            opcjonalny model językowy z bezpiecznikami (weryfikacja cytatów, maskowanie danych osobowych)
rm_docx.py           raporty Markdown → Word (.docx), bez bibliotek
landing.html, login.html, bidradar_ui.html   strony (samodzielne pliki, działają offline)
konfiguracja/        podsektory, wagi, źródła
tools/               szablony stron i skrypt build_pages.py
tests/               testy automatyczne
deploy/              instalator, usługi systemd
docs/ARCHITEKTURA.md opis architektury i modelu danych
```

## Dane i licencje

Granica Polski i krajów sąsiednich: Natural Earth (domena publiczna). Granice województw: GADM (przez pakiet npm @dkkoval/react-pl-stats-map, Apache-2.0; dane GADM do użytku niekomercyjnego). Geokodowanie: Nominatim / OpenStreetMap (ODbL). Ogłoszenia: Biuletyn Zamówień Publicznych, TED. Paczka organizatora (arkusz, kopia BZP) nie jest częścią repozytorium.
