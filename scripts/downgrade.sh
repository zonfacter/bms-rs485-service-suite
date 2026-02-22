#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICES=(jk-ble-mqtt-gateway daly-ble-mqtt-gateway ble-scan-store nodered)

log() { printf "\n[%s] %s\n" "$(date +%H:%M:%S)" "$*"; }
warn() { printf "  [WARN] %s\n" "$*"; }
ok() { printf "  [OK] %s\n" "$*"; }

ask_yes_no() {
  local prompt="$1"
  local default="${2:-n}"
  local hint
  if [[ "$default" == "y" ]]; then hint="[Y/n]"; else hint="[y/N]"; fi
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

choose_tag() {
  local tags
  tags="$(git -C "$REPO_DIR" tag --list 'v*' --sort=-version:refname)"
  if [[ -z "$tags" ]]; then
    echo ""
    return
  fi

  echo "Verfuegbare Versionen:"
  local i=1
  while IFS= read -r t; do
    printf "  %2d) %s\n" "$i" "$t"
    i=$((i+1))
  done <<< "$tags"

  while true; do
    read -r -p "Bitte Zielversion eingeben (z.B. v1.0.1): " picked
    if git -C "$REPO_DIR" rev-parse -q --verify "refs/tags/$picked" >/dev/null; then
      echo "$picked"
      return
    fi
    echo "Ungueltige Version: $picked"
  done
}

TARGET_TAG="${1:-}"

log "Repo: $REPO_DIR"
log "Hole aktuelle Tags vom Remote"
git -C "$REPO_DIR" fetch --tags --prune
ok "Tags aktualisiert"

if [[ -z "$TARGET_TAG" ]]; then
  TARGET_TAG="$(choose_tag)"
  if [[ -z "$TARGET_TAG" ]]; then
    echo "Keine Tags gefunden. Abbruch."
    exit 1
  fi
fi

if ! git -C "$REPO_DIR" rev-parse -q --verify "refs/tags/$TARGET_TAG" >/dev/null; then
  echo "Tag nicht gefunden: $TARGET_TAG"
  exit 1
fi

CUR_REF="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD || true)"
CUR_SHA="$(git -C "$REPO_DIR" rev-parse --short HEAD || true)"
log "Aktuell: ${CUR_REF:-detached} @ ${CUR_SHA:-unknown}"
log "Ziel: $TARGET_TAG"

if [[ -n "$(git -C "$REPO_DIR" status --porcelain)" ]]; then
  warn "Arbeitsverzeichnis hat uncommittete Aenderungen."
  if ! ask_yes_no "Trotzdem fortfahren?" n; then
    echo "Abbruch."
    exit 1
  fi
fi

if ! ask_yes_no "Auf $TARGET_TAG wechseln (detached HEAD)?" y; then
  echo "Abbruch."
  exit 0
fi

git -C "$REPO_DIR" checkout "$TARGET_TAG"
ok "Checkout auf $TARGET_TAG abgeschlossen"

if ask_yes_no "Dienste jetzt neu starten?" y; then
  sudo systemctl restart "${SERVICES[@]}"
  sleep 2
  for s in "${SERVICES[@]}"; do
    state="$(systemctl is-active "$s" || true)"
    printf "  %-28s %s\n" "$s" "$state"
  done
fi

cat <<MSG

Downgrade abgeschlossen.

Aktive Version: $TARGET_TAG
Hinweis:
- Du bist jetzt im detached HEAD Zustand.
- Zurueck auf main:
    git -C "$REPO_DIR" checkout main
    git -C "$REPO_DIR" pull
MSG
