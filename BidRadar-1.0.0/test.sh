#!/usr/bin/env bash
cd "$(dirname "$0")"
APP="$PWD"
[ $# -ge 1 ] || { echo "Użycie: bash test.sh PLIK [PLIK ...]"; exit 1; }
up(){ python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8010/info',timeout=3)" 2>/dev/null; }
if ! up; then
  MIRROR="$(find /home /root -name bzp_mirror.py 2>/dev/null | head -1)"
  if [ -n "$MIRROR" ]; then
    echo "… uruchamiam kopię BZP: $MIRROR"
    (cd "$(dirname "$MIRROR")" && nohup python3 bzp_mirror.py > "$APP/kopia_bzp.log" 2>&1 &)
    for i in $(seq 1 180); do up && break; sleep 1; done
  fi
fi
FILES=()
for f in "$@"; do FILES+=("$(realpath "$f")"); done
python3 "$APP/bidradar_test.py" "${FILES[@]}" --out "$APP/wyniki_testu"
