# Installation Assistant (Benutzerfuehrung)

Das Repo enthaelt ein interaktives Installationsskript:

- Script: `scripts/install_suite.sh`

## Start

```bash
cd /home/black/bms-rs485-service-suite
bash scripts/install_suite.sh
```

## Was der Assistent macht

- Prueft Basis-Kommandos (`sudo`, `systemctl`, `python3`)
- Optional: prueft/instaliert Python-Abhaengigkeiten (`bleak`, `paho-mqtt`) in `.venv`
- Installiert systemd Units:
  - `daly-ble-mqtt-gateway.service`
  - `jk-ble-mqtt-gateway.service`
  - `ble-scan-store.service`
- Erzeugt fehlende lokale Configs aus `*.example.json`
- Aktiviert/Startet Services (optional)
- Fuehrt Status- und MQTT-Livecheck aus (optional)

Jeder Schritt wird abgefragt (ja/nein).

## Hinweise

- Bestehende Config-Dateien werden nicht ueberschrieben.
- Lokale Configs (`config/*.json`) bleiben lokal und werden nicht ins Git committet.
- Nach Aenderungen an Config/Service:

```bash
sudo systemctl daemon-reload
sudo systemctl restart daly-ble-mqtt-gateway jk-ble-mqtt-gateway ble-scan-store
```
