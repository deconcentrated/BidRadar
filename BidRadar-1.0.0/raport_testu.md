# BidRadar – raport z testu działania

*Wygenerowano 2026-11-21 19:53 skryptem `bidradar_eval.py` na świeżej bazie · BZP: http://127.0.0.1:8010 · tryb analizy: reguły + statystyka*

## 1. Podsumowanie

- **Projekty z arkusza MFiPR: 22** – wzbogaconych automatycznie z BZP w **6 s** (140 zapytań do API, bez ręcznych wpisów).
- **14 z 22** połączonych automatycznie z podwójnym potwierdzeniem (numer ogłoszenia **i** NIP zamawiającego); **8** przekazanych do decyzji eksperta z uzasadnieniem; **0** bez dopasowania.
- Z 15 projektów z numerem BZP: 14 połączone poprawnie, 1 odtworzone z odwołań w innych ogłoszeniach (brak ogłoszenia o zamówieniu w kopii BZP).
- Z 7 projektów KPO (bez numeru BZP): 7 z kandydatem dopasowania (NIP + nazwa), zawsze do decyzji eksperta.
- Odczytane: **ogłoszenia 99**, **umowy 31**, **zdarzenia ryzyka 53** – każde ze źródłem (ogłoszenie + pole + cytat).
- Grupa porównawcza: **26842 zakończonych umów** z BZP zbudowana w 23 s.
- Dokumenty: **3 z 3** przetworzonych w 1 s (w tym skan przez OCR), 19 fragmentów R01–R15, weryfikacja z BZP: {'zgodne': 6, 'uwaga': 1, 'niezgodne': 2, 'brak w BZP': 1}.
- Nowa inwestycja (przykład): 0.0 s · rekord testowy: 0.3 s.

## 2. Łączenie projektów z BZP

| Status | Liczba | Udział |
|---|---|---|
| poprawne | 14 | 64% |
| niepewne | 8 | 36% |

| Metoda | Liczba |
|---|---|
| numer BZP → OCID | 14 |
| dopasowanie rozmyte (NIP + nazwa) | 7 |
| odwołania w innych ogłoszeniach | 1 |

Kontrola NIP zgodna w 22 najlepszych powiązaniach. Zasada: pewność 100% tylko przy zgodnym numerze ogłoszenia **i** NIP; wszystko inne trafia do kolejki eksperta (brak w kopii BZP ≠ brak publikacji).

| Lp. | Odbiorca | Identyfikator | Status | Metoda | Podstawa |
|---|---|---|---|---|---|
| 1 | Gmina Oława | 2022/BZP 00058537/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 2 | Gmina Zagrodno | KPOD.03.15-IW.13-0004/25 | niepewne | dopasowanie rozmyte (NIP + nazwa) | podobieństwo nazwy 99% + NIP zgodny → do decyzji eksperta |
| 3 | Gmina Czernikowo | 2024/BZP 00349631/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 4 | Gmina Czernikowo | 2025/BZP 00569258/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 5 | Gmina Zławieś Wielka | KPOD.03.15-IW.14-0032/25 | niepewne | dopasowanie rozmyte (NIP + nazwa) | podobieństwo nazwy 100% + NIP zgodny → do decyzji eksperta |
| 6 | Gmina Gościeradów | 2024/BZP 00067908/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 7 | Gmina Kamionka | KPOD.03.15-IW.15-0081/25 | niepewne | dopasowanie rozmyte (NIP + nazwa) | podobieństwo nazwy 99% + NIP zgodny → do decyzji eksperta |
| 8 | Gmina Biecz | 2026/BZP 00084760/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 9 | Gmina Zakrzew | 2025/BZP 00307948/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 10 | Gmina Grodzisko Dolne | KPOD.03.15-IW.21-0025/25 | niepewne | dopasowanie rozmyte (NIP + nazwa) | podobieństwo nazwy 100% + NIP zgodny → do decyzji eksperta |
| 11 | Gmina Sanok | 2023/BZP 00271948/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 12 | Gmina Kartuzy – Urząd Miejski w Kartuzach | 2025/BZP 00119683/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 13 | Gmina Bestwina | 2023/BZP 00206497/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 14 | Gmina Bobrowniki | 2026/BZP 00130634/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 15 | Gmina Elbląg | 2023/BZP 00135041/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 16 | Gmina Miłakowo | 2022/BZP 00308854/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 17 | Gmina Kleszczewo | KPOD.03.15-IW.27-0013/25 | niepewne | dopasowanie rozmyte (NIP + nazwa) | podobieństwo nazwy 100% + NIP zgodny → do decyzji eksperta |
| 18 | Gmina Zduny | KPOD.03.15-IW.27-0107/25 | niepewne | dopasowanie rozmyte (NIP + nazwa) | podobieństwo nazwy 99% + NIP zgodny → do decyzji eksperta |
| 19 | Wielkopolski Zarząd Dróg Wojewódzkich w Poznaniu | 2024/BZP 00354928/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 20 | Gmina Nowogard | KPOD.03.15-IW.28-0003/24 | niepewne | dopasowanie rozmyte (NIP + nazwa) | podobieństwo nazwy 100% + NIP zgodny → do decyzji eksperta |
| 21 | Gmina Nowogard | 2025/BZP 00364116/01 | poprawne | numer BZP → OCID | numer ogłoszenia + NIP zgodne |
| 22 | Gmina Wolin | 2023/BZP 00110672/01 | niepewne | odwołania w innych ogłoszeniach | odtworzone z odwołań w późniejszych ogłoszeniach → do decyzji eksperta |

## 3. Dane z ogłoszeń

| Typ ogłoszenia | Liczba |
|---|---|
| ogłoszenie o wykonaniu umowy | 32 |
| ogłoszenie o zamówieniu | 27 |
| ogłoszenie o wyniku | 26 |
| ogłoszenie o zmianie umowy | 14 |

Umowy: 31 · wartość 105 499 268 zł · wypłacono 116 270 300 zł · po terminie: 23 (74%).
Jakość danych: postępowania ze zduplikowanym ogłoszeniem o wykonaniu umowy: 1 (liczone raz); luki odtworzone z odwołań: 1.

| Kod | Zdarzenia ryzyka |
|---|---|
| R01 | 5 |
| R01/R07 | 8 |
| R06 | 23 |
| R07 | 9 |
| R11/R12 | 4 |
| R12 | 4 |

## 4. Grupa porównawcza (zakończone umowy z BZP)

| Pole | Pokrycie |
|---|---|
| wartość umowy (4.4) | 100% |
| wypłacono (5.5) | 100% |
| termin planowany (4.2, data lub okres) | 100% |
| termin wykonania (5.2) | 97% |
| w terminie (5.3) | 100% |
| zmiana kosztu (wiarygodna, ±200%) | 99% |

Podsektory (klasyfikacja MFiPR z przedmiotu zamówienia): Inne obiekty wod-kan 13753, Kanalizacja sanitarna 5879, Sieć wodociągowa 2812, Kanalizacja deszczowa i retencja 1965, Oczyszczalnia ścieków 1324, Uzdatnianie i zaopatrzenie w wodę 1109.

## 5. Dokumenty

| Dokument | Format | Powiązanie | Fragmenty | Weryfikacja z BZP |
|---|---|---|---|---|
| Risk_Miner_pismo_termin_grodzisko.pdf | PDF (skan, OCR) | RM-2026-010 (numer projektu KPO, 100%) | 6 | zgodne: 1 |
| Risk_Miner_protokol_koniecznosci_aneksowania_zagrodno.pdf | PDF | RM-2026-002 (numer projektu KPO, 100%) | 6 | zgodne: 2, uwaga: 1 |
| Risk_Miner_protokol_koniecznosci_zlawies.docx | DOCX | RM-2026-005 (nazwa zadania + gmina, 95%) | 7 | niezgodne: 2, zgodne: 3, brak w BZP: 1 |

## 6. Analiza nowej inwestycji (przykład)

- Plik: przyklad_opis_inwestycji.docx (DOCX, 39 zdań) · czas 0.0 s
- Gotowość opisu wg checklisty eksperckiej: **81%** · ocena ryzyka: **wysokie**
- Fakty z cytatem: 20 · sygnały ryzyka z cytatem: 15 · sygnały z danych BZP: 2 · brakujące informacje: 4
- Grupa porównawcza: 114 umów (podsektor „Sieć wodociągowa”, cała Polska, wartość umowy 4,7 mln zł–42,4 mln zł)
- Checklista: 1. ujęte; 2. ujęte; 3. ujęte; 4. ujęte; 5. ujęte, sprawa otwarta (brak potwierdzenia); 6. ujęte; 7. częściowo; 8. ujęte, sprawa otwarta (brak potwierdzenia); 9. częściowo

## 7. Rekord testowy (przykład)

- Gmina Gać · 2023/BZP 00129088/01 → poprawne · ocena: ryzyko · zmiana kosztu 40.6% · zdarzenia: 3 · ogłoszenia: 4 · czas 0.3 s

## 8. Powtarzalność i ograniczenia

- Wynik deterministyczny: te same dane wejściowe → te same liczby (reguły + statystyka). Model językowy, jeśli włączony, tylko dokłada propozycje z cytatem sprawdzonym w dokumencie.
- Kopia BZP organizatora: brak ogłoszenia w kopii nie oznacza braku publikacji – takie przypadki są oznaczone i trafiają do eksperta.
- Projekty KPO nie mają numeru BZP – dopasowanie jest zawsze propozycją do zatwierdzenia.
- Brak zbioru referencyjnego od organizatora: „poprawne” oznacza podwójne potwierdzenie (numer + NIP), a nie ocenę z zewnętrznego wzorca.
