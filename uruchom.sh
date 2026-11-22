#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
APP="$PWD"
ok(){ printf "  \033[32m✓\033[0m %s\n" "$1"; }
warn(){ printf "  \033[33m!\033[0m %s\n" "$1"; }
fail(){ printf "  \033[31m✗\033[0m %s\n" "$1"; exit 1; }

for a in "$@"; do
  case "$a" in
    --siec|--sieć) export RM_HOST=0.0.0.0 ;;
    --bez-logowania) export RM_AUTH=off ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) fail "Nieznana opcja: $a (dostępne: --siec, --bez-logowania)" ;;
  esac
done

echo "BidRadar – przygotowanie"

command -v python3 >/dev/null || fail "Brak python3 – zainstaluj: sudo apt install python3"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail "Potrzebny Python 3.10 lub nowszy (masz: $(python3 --version))."
ok "Python: $(python3 --version)"

[ -f .env ] && ok "Konfiguracja: .env" || ok "Konfiguracja: domyślna (wzór zmian: config.example.env)"

XLSX="${XLSX:-${RM_XLSX:-$(find "$APP" /home /root /data -maxdepth 6 -name 'Risk_Miner_dane_projekty.xlsx' 2>/dev/null | head -1)}}"
[ -n "$XLSX" ] || fail "Nie znaleziono Risk_Miner_dane_projekty.xlsx – rozpakuj paczkę startową organizatora (np. do folderu paczka/)."
ok "Arkusz: $XLSX"

BZP="${RM_BZP_URL:-http://127.0.0.1:8010}"
up(){ python3 -c "import urllib.request,sys;urllib.request.build_opener(urllib.request.ProxyHandler({})).open('$BZP/info',timeout=3)" 2>/dev/null; }
if up; then
  ok "Kopia BZP działa ($BZP)"
else
  MIRROR="$(find "$APP" /home /root /data -maxdepth 6 -name bzp_mirror.py 2>/dev/null | head -1)"
  [ -n "$MIRROR" ] || fail "Nie znaleziono bzp_mirror.py – rozpakuj risk_miner_bzp_kopia.zip od organizatora."
  echo "  … uruchamiam kopię BZP: $MIRROR (pierwszy raz ok. 1–2 min)"
  (cd "$(dirname "$MIRROR")" && nohup python3 bzp_mirror.py > "$APP/kopia_bzp.log" 2>&1 &)
  for _ in $(seq 1 180); do up && break; sleep 1; done
  up || fail "Kopia BZP nie wystartowała – zobacz $APP/kopia_bzp.log"
  ok "Kopia BZP działa (log: kopia_bzp.log)"
fi

if command -v pdftotext >/dev/null && command -v tesseract >/dev/null && tesseract --list-langs 2>/dev/null | grep -qx pol; then
  ok "PDF i OCR: poppler + tesseract (pol)"
else
  warn "Brak narzędzi do PDF/OCR. Zainstaluj (raz): sudo apt install poppler-utils tesseract-ocr tesseract-ocr-pol"
fi

if pkill -f "$APP/server.py" 2>/dev/null; then ok "Zatrzymano poprzednią instancję BidRadar"; sleep 1; fi

echo
echo "Start BidRadar. Adres strony podam za chwilę w linii „BidRadar działa”."
[ "$RM_HOST" = "0.0.0.0" ] && echo "Tryb sieciowy: aplikacja będzie dostępna z innych komputerów (otwórz port 8000 w zaporze)."
echo "Zatrzymanie: Ctrl+C (kopia BZP działa dalej w tle)"
echo
exec python3 "$APP/server.py" --xlsx "$XLSX"
