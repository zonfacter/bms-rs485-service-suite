# Changelog

## v1.0.2 - 2026-02-22

### Added
- JK BLE Gateway Auto-Recovery:
  - erkennt typische BlueZ-Haenger (`InProgress`, `Notify acquired`)
  - fuehrt Adapter-Reset mit Cooldown aus
  - macht einen direkten Retry-Read nach Reset
- Neue JK Config-Felder:
  - `bt_reset_on_failures`
  - `bt_reset_cooldown_s`

### Changed
- JK Gateway Runtime-Config (`cmd/config`) erweitert um Auto-Recovery Parameter.
- JK Doku um Auto-Recovery und neue Konfigurationsfelder ergaenzt.
- BLE Scan Konfiguration angepasst, um Kollisionen auf `hci2` zu reduzieren.

## v1.0.1 - 2026-02-18

### Added
- Interaktives Installationsskript mit Benutzerfuehrung:
  - `scripts/install_suite.sh`
  - Schrittweise Ja/Nein-Fuehrung fuer Service-Install, Config-Setup, Statuschecks

### Documentation
- Neue Anleitung:
  - `docs/INSTALLATION_ASSISTANT.md`
- README um Abschnitt "Interaktive Installation" erweitert.
- Doku-Index (`docs/INDEX.md`) um Installations-Assistent ergaenzt.

## v1.0.0 - 2026-02-17

### Added
- DALY BLE Gateway Erweiterungen:
  - Adapter-Fallback pro Device (`adapter_fallbacks`)
  - `state` Topic pro Device (`online/stale/last_ok_age/failures/error`)
  - Fleet Aggregation (`bms/daly/fleet/raw`, `bms/daly/fleet/online`)
  - SoC-Hysterese fuer Fleet (`group_soc_low_pct`, `group_soc_high_pct`)
  - Trennung von `sample_interval_s` und `publish_interval_s`
  - Optionales `publish_on_change`
  - Optionales BLE Adapter Reset bei Dauerfehlern
- BLE Scan Store:
  - `adapter_cycle` fuer HCI-Rotation (`hci0/hci1/hci2`)
- Neue Dokumentation:
  - `docs/BLE_SCAN_STORE.md`
  - `docs/THIRD_PARTY_SOURCES.md`

### Changed
- DALY Influx Write Logik in Node-RED auf Change-Driven + Heartbeat reduziert.
- DALY und BLE Konfigurationsbeispiele auf aktuelle Felder aktualisiert.
- README und Dokumentationsindex neu strukturiert.
- Influx Doku auf `rp48h` mit `DURATION INF` aktualisiert.

### Removed / Deprecated
- Direkte Empfehlung fester Einzeladapter ohne Fallback aus der Doku entfernt.
- `poll_interval_s` gilt nur noch als Kompatibilitaetsalias fuer `sample_interval_s`.

### Notes
- Keine direkte Codeuebernahme aus Drittprojekten; nur konzeptionelle Orientierung.
- Quellen und Lizenzhinweise: `docs/THIRD_PARTY_SOURCES.md`.
