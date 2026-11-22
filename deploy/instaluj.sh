#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8000}"
DOMENA="${2:-}"
APP="$(cd "$(dirname "$0")/.." && pwd)"
ok(){ printf "  \033[32m✓\033[0m %s\n" "$1"; }
warn(){ printf "  \033[33m!\033[0m %s\n" "$1"; }
fail(){ printf "  \033[31m✗\033[0m %s\n" "$1"; exit 1; }

[ "$(id -u)" = 0 ] || fail "Uruchom jako root: sudo -i, potem ponownie."
echo "BidRadar – instalacja na serwerze ($APP, port $PORT)"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 unzip curl poppler-utils tesseract-ocr tesseract-ocr-pol antiword >/dev/null
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail "Potrzebny Python 3.10+ (jest: $(python3 --version))."
ok "Narzędzia: $(python3 --version), poppler, tesseract (pol), antiword"

mkdir -p "$APP/paczka"
XLSX="$(find "$APP/paczka" /root /home -maxdepth 3 -name 'Risk_Miner_dane_projekty.xlsx' 2>/dev/null | head -1 || true)"
[ -n "$XLSX" ] || fail "Brak Risk_Miner_dane_projekty.xlsx (wgraj do /root albo $APP/paczka)."
[ "$XLSX" = "$APP/paczka/Risk_Miner_dane_projekty.xlsx" ] || cp "$XLSX" "$APP/paczka/"
XLSX="$APP/paczka/Risk_Miner_dane_projekty.xlsx"
ok "Rekordy projektów: $XLSX"

MIRROR="$(find "$APP/paczka" /root /home -maxdepth 5 -name bzp_mirror.py 2>/dev/null | grep -v _poprzedni | head -1 || true)"
if [ -z "$MIRROR" ]; then
  ZIP="$(find /root /home "$APP/paczka" -maxdepth 3 -name 'risk_miner_bzp_kopia*.zip' 2>/dev/null | head -1 || true)"
  [ -n "$ZIP" ] || fail "Brak kopii BZP (risk_miner_bzp_kopia.zip) w /root."
  echo "  … rozpakowuję kopię BZP ($(du -h "$ZIP" | cut -f1))"
  unzip -q -o "$ZIP" -d "$APP/paczka/"
  MIRROR="$(find "$APP/paczka" -maxdepth 4 -name bzp_mirror.py | head -1 || true)"
  [ -n "$MIRROR" ] || fail "W archiwum nie ma bzp_mirror.py."
fi
ok "Kopia BZP: $MIRROR"

if [ -n "${RM_DEMO_PASSWORD:-}" ]; then
  touch "$APP/.env"; sed -i '/^RM_DEMO_PASSWORD=/d' "$APP/.env"; echo "RM_DEMO_PASSWORD=$RM_DEMO_PASSWORD" >> "$APP/.env"
  ok "Hasło kont startowych z RM_DEMO_PASSWORD zapisane w .env"
fi

if [ "${CADDY:-0}" = 1 ]; then HOST=127.0.0.1; SECURE=1; else HOST=0.0.0.0; SECURE=0; fi

cat > /etc/systemd/system/bidradar-bzp.service <<EOF
[Unit]
Description=Kopia API Biuletynu Zamówień Publicznych dla BidRadar
After=network.target

[Service]
WorkingDirectory=$(dirname "$MIRROR")
ExecStart=/usr/bin/python3 bzp_mirror.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/bidradar.service <<EOF
[Unit]
Description=BidRadar – mapa ryzyk inwestycji wodno-kanalizacyjnych
After=network-online.target bidradar-bzp.service
Wants=bidradar-bzp.service

[Service]
WorkingDirectory=$APP
EnvironmentFile=-$APP/.env
Environment=RM_HOST=$HOST RM_PORT=$PORT RM_SECURE_COOKIE=$SECURE PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 $APP/server.py --xlsx $XLSX
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

pkill -f "$APP/server.py" 2>/dev/null || true
pkill -f "bzp_mirror.py" 2>/dev/null || true
sleep 1
systemctl daemon-reload
systemctl enable -q bidradar-bzp bidradar
systemctl restart bidradar-bzp
echo "  … czekam na kopię BZP (pierwszy raz 1–2 min)"
for _ in $(seq 1 180); do curl -sf -o /dev/null http://127.0.0.1:8010/info && break; sleep 1; done
curl -sf -o /dev/null http://127.0.0.1:8010/info || fail "Kopia BZP nie odpowiada: journalctl -u bidradar-bzp -n 30"
ok "Kopia BZP działa (usługa bidradar-bzp)"
systemctl restart bidradar
for _ in $(seq 1 90); do curl -sf -o /dev/null "http://127.0.0.1:$PORT/healthz" && break; sleep 2; done
curl -sf -o /dev/null "http://127.0.0.1:$PORT/healthz" || fail "BidRadar nie wystartował: journalctl -u bidradar -n 40"
ok "BidRadar działa (usługa bidradar, restart po awarii i po starcie maszyny)"

if [ "${CADDY:-0}" = 1 ]; then
  if [ -z "$DOMENA" ]; then
    IP="$(curl -sf -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || curl -sf https://ifconfig.me || true)"
    [ -n "$IP" ] || fail "Nie znam zewnętrznego IP – podaj domenę: CADDY=1 bash deploy/instaluj.sh $PORT 1-2-3-4.sslip.io"
    DOMENA="$(echo "$IP" | tr . -).sslip.io"
  fi
  if ! command -v caddy >/dev/null; then
    apt-get install -y -qq caddy >/dev/null 2>&1 || {
      apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
      curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
      curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
      apt-get update -qq && apt-get install -y -qq caddy >/dev/null; }
  fi
  cat > /etc/caddy/Caddyfile <<EOF
$DOMENA {
	encode gzip
	reverse_proxy 127.0.0.1:$PORT
}
EOF
  systemctl enable -q caddy
  systemctl restart caddy
  ok "Caddy: HTTPS dla $DOMENA (certyfikat Let's Encrypt pobierze się w ciągu minuty)"
  URL="https://$DOMENA"
else
  IP="$(curl -sf -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || hostname -I | awk '{print $1}')"
  URL="http://$IP:$PORT"
fi

echo
echo "Gotowe:"
echo "  Strona:  $URL/"
echo "  Panel:   $URL/app"
echo "  Logowanie eksperta: $URL/login (hasła: $APP/hasla_startowe.txt albo RM_DEMO_PASSWORD)"
echo
echo "Przydatne: systemctl status bidradar · journalctl -u bidradar -f · systemctl restart bidradar"
[ "${CADDY:-0}" = 1 ] && echo "Zapora Google Cloud musi przepuszczać porty 80 i 443 (maszyna: Allow HTTP / HTTPS traffic)."
exit 0
