#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_DIR="$REPO_DIR/systemd"
CONFIG_DIR="$REPO_DIR/config"
ENV_FILE="$REPO_DIR/.env"

log() { printf "\n[%s] %s\n" "$(date +%H:%M:%S)" "$*"; }
ok() { printf "  [OK] %s\n" "$*"; }
warn() { printf "  [WARN] %s\n" "$*"; }
err() { printf "  [ERR] %s\n" "$*" >&2; }

require_cmd() {
  local c="$1"
  command -v "$c" >/dev/null 2>&1 || {
    err "Befehl fehlt: $c"
    return 1
  }
}

ask_yes_no() {
  local prompt="$1"
  local default="${2:-y}" # y|n
  local hint
  if [[ "$default" == "y" ]]; then
    hint="[Y/n]"
  else
    hint="[y/N]"
  fi
  while true; do
    read -r -p "$prompt $hint " ans
    ans="${ans:-$default}"
    case "${ans,,}" in
      y|yes|j|ja) return 0 ;;
      n|no|nein) return 1 ;;
      *) echo "Bitte y oder n eingeben." ;;
    esac
  done
}

install_unit() {
  local unit="$1"
  local src="$SYSTEMD_DIR/$unit"
  local dst="/etc/systemd/system/$unit"
  [[ -f "$src" ]] || { warn "Unit fehlt im Repo: $src"; return 0; }
  sudo cp -a "$src" "$dst"
  ok "Installiert: $dst"
}

ensure_config() {
  local cfg="$1"
  local ex="$2"
  if [[ -f "$cfg" ]]; then
    ok "Config vorhanden: $cfg"
    return 0
  fi
  if [[ -f "$ex" ]]; then
    cp -a "$ex" "$cfg"
    ok "Config aus Beispiel erzeugt: $cfg"
  else
    warn "Keine Config und kein Beispiel gefunden: $cfg"
  fi
}

show_header() {
  cat <<'EOF'
============================================================
 BMS RS485 Service Suite - Interaktive Installation
============================================================
Dieses Skript richtet die Services und Basis-Konfiguration ein:
 - daly-ble-mqtt-gateway.service
 - jk-ble-mqtt-gateway.service
 - ble-scan-store.service

Hinweis:
 - Das Skript fragt jeden wichtigen Schritt ab.
 - Es werden keine bestehenden Config-Dateien überschrieben.
EOF
}

main() {
  show_header
  log "Repo: $REPO_DIR"

  require_cmd sudo
  require_cmd systemctl
  require_cmd python3
  require_cmd mosquitto_sub || warn "mosquitto_sub nicht gefunden (nur für Live-Checks relevant)."

  if ask_yes_no "Soll geprüft werden, ob Python-Abhängigkeiten (bleak, paho-mqtt) installiert sind?" y; then
    if python3 -c "import bleak, paho.mqtt.client" >/dev/null 2>&1; then
      ok "Python-Abhängigkeiten sind vorhanden."
    else
      warn "Abhängigkeiten fehlen in system-python."
      if [[ -x "$REPO_DIR/.venv/bin/pip" ]]; then
        if ask_yes_no "Abhängigkeiten in .venv installieren (bleak, paho-mqtt)?" y; then
          "$REPO_DIR/.venv/bin/pip" install --upgrade pip bleak paho-mqtt
          ok "Abhängigkeiten in .venv installiert."
        fi
      else
        warn ".venv/pip nicht gefunden. Bitte Abhängigkeiten manuell installieren."
      fi
    fi
  fi

  if ask_yes_no "Systemd-Units nach /etc/systemd/system kopieren?" y; then
    install_unit "daly-ble-mqtt-gateway.service"
    install_unit "jk-ble-mqtt-gateway.service"
    install_unit "ble-scan-store.service"
    sudo systemctl daemon-reload
    ok "systemd daemon-reload ausgeführt."
  fi

  if ask_yes_no "Fehlende lokale Config-Dateien aus *.example.json erzeugen?" y; then
    ensure_config "$CONFIG_DIR/daly_ble_gateway.json" "$CONFIG_DIR/daly_ble_gateway.example.json"
    ensure_config "$CONFIG_DIR/jk_ble_gateway.json" "$CONFIG_DIR/jk_ble_gateway.example.json"
    ensure_config "$CONFIG_DIR/ble_scan_store.json" "$CONFIG_DIR/ble_scan_store.example.json"
  fi

  if ask_yes_no "Optional: .env Vorlage erzeugen/ergänzen?" n; then
    {
      [[ -f "$ENV_FILE" ]] || echo "# Lokale Umgebungswerte für Services"
      echo "BMS_BLE_LOCK_PATH=/tmp/bms_ble.lock"
      echo "BMS_BLE_LOCK_TIMEOUT_S=40"
    } >>"$ENV_FILE"
    ok ".env ergänzt: $ENV_FILE"
  fi

  if ask_yes_no "Services aktivieren und starten?" y; then
    sudo systemctl enable --now daly-ble-mqtt-gateway.service
    sudo systemctl enable --now jk-ble-mqtt-gateway.service
    sudo systemctl enable --now ble-scan-store.service
    ok "Services wurden aktiviert und gestartet."
  fi

  if ask_yes_no "Statusprüfung anzeigen?" y; then
    echo
    systemctl is-active daly-ble-mqtt-gateway.service || true
    systemctl is-active jk-ble-mqtt-gateway.service || true
    systemctl is-active ble-scan-store.service || true
  fi

  if ask_yes_no "Kurzen MQTT-Livecheck (10s) für DALY Online-Topics starten?" y; then
    timeout 10s mosquitto_sub -h 127.0.0.1 -t 'bms/daly/+/online' -v || true
  fi

  cat <<'EOF'

============================================================
 Installation abgeschlossen
============================================================
Nächste Schritte:
1) Config prüfen:
   - config/daly_ble_gateway.json
   - config/jk_ble_gateway.json
   - config/ble_scan_store.json
2) Logs prüfen:
   sudo journalctl -u daly-ble-mqtt-gateway -n 200 --no-pager
   sudo journalctl -u jk-ble-mqtt-gateway -n 200 --no-pager
   sudo journalctl -u ble-scan-store -n 200 --no-pager
3) Dashboard:
   http://<pi-ip>:1880/ui
EOF
}

main "$@"
