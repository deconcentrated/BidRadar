# Jak dobrze dane z BZP przewidują ryzyko umowy – ocena BidRadar

Umowy z ogłoszeń o wykonaniu (kopia BZP): **26595** z kompletem danych o terminie i koszcie. „Zagrożona” = po terminie albo koszt ≥ +3%: **42,7%** umów. Cechy: tylko znane przed realizacją.

## Wyniki

| Metoda | AUROC (5-krotna CV) | Brier (CV) | AUROC – test na 2025–2026 |
|---|---|---|---|
| Grupa porównawcza BidRadar (podsektor → wartość → województwo) | 0,663 ± 0,011 | 0,225 | 0,667 |
| Grupa porównawcza + planowany czas realizacji | 0,663 ± 0,009 | 0,225 | 0,668 |
| Regresja logistyczna, te same cechy (scikit-learn) | 0,672 ± 0,011 | 0,223 | 0,677 |
| Gradient boosting, te same cechy (scikit-learn) | 0,680 ± 0,010 | 0,221 | 0,675 |
| Bez modelu (ten sam odsetek dla każdej umowy) | 0,500 | 0,252 | 0,500 |

AUROC: szansa, że z pary (umowa zagrożona, niezagrożona) metoda wyżej oceni tę zagrożoną. 0,5 = rzut monetą, 1 = ideał.

## Czy przedziały BidRadar są uczciwe

Rzeczywista zmiana kosztu mieściła się w przedziale P10–P90 grupy porównawczej w **81,5%** umów (walidacja krzyżowa) i w **79,9%** umów z lat 2025–2026 (cel: 80%).

Kalibracja (grupa porównawcza + czas, walidacja krzyżowa, decyle prognozy):

| Prognozowany odsetek zagrożonych | Rzeczywisty odsetek | Umów |
|---|---|---|
| 18,4% | 20,2% | 2659 |
| 25,8% | 26,2% | 2660 |
| 32,2% | 34,0% | 2659 |
| 36,2% | 34,4% | 2660 |
| 40,2% | 39,5% | 2659 |
| 44,6% | 44,6% | 2660 |
| 49,0% | 49,3% | 2659 |
| 54,5% | 54,8% | 2660 |
| 60,4% | 59,8% | 2659 |
| 67,3% | 64,7% | 2660 |

## Wniosek

Z informacji znanych przed przetargiem ryzyka pojedynczej umowy nie da się przewidzieć dokładnie – żadna metoda, także ML, nie zbliża się do AUROC 0,8. Dlatego BidRadar nie podaje jednej „pewnej” liczby, tylko odsetek i przedziały z grupy podobnych umów z liczebnością próby i linkami – a ich trafność (kalibracja, pokrycie P10–P90) sprawdziliśmy powyżej.

*Wygenerowano: `python3 tools/ocena_predykcji.py --db bidradar.db` (wiersze ze scikit-learn tylko, gdy biblioteka jest zainstalowana; aplikacja jej nie potrzebuje); ziarno losowania 42, wynik powtarzalny.*
