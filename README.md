# BMS RS485 Service Suite (Raspberry Pi + Node-RED)

Node-RED Erweiterung fuer Deye/Pylontech-kompatible BMS-Kommunikation ueber RS485.

## Enthalten
- Service-Tab `RS485 Service` mit:
- `Service Presets` (GET_42/44/47/51/83, GET_B0_INFO, GET_B0_CAP, CTRL_45_0D/0F)
- `Profil -> BMS schreiben (CID2=49)` fuer Kern-Limits
- Expert-Frame-Builder
- Decoder-Erweiterungen fuer Set-ACK auf Services `49`, `45`, `8B`
- JK-BMS BLE -> MQTT Gateway (stabile Daten fuer Node-RED + andere Systeme)
- DALY SmartBMS / DALY Balancer BLE -> MQTT Gateway (Akku-1/2/3)
- InfluxDB Export (Retention `rp48h`) fuer JK BLE, DALY BLE und RS485 + fertiges Grafana Dashboard JSON
- Dokumentation fuer Betrieb, Sicherheit und Troubleshooting

## Schnellstart
1. Dashboard oeffnen: `http://<dein-pi>:1880/ui`
2. Tab `RS485 Service` aufrufen
3. Immer zuerst mit `Dry-Run` testen
4. Danach erst echte Schreibvorgaenge (`Dry-Run` aus)

## Wichtige Sicherheit
- Schreib-Frames aendern BMS-Parameter dauerhaft.
- Nur plausible Werte schreiben.
- Vor jeder Aenderung `flows.json` sichern.

## Dateistruktur
- `docs/INDEX.md` Inhaltsverzeichnis (Start hier)
- `docs/DASHBOARD_SERVICE.md` Bedienung Service-Seite
- `docs/MQTT_GATEWAY_JK_BLE.md` JK BLE via MQTT (Topics + Service + Schema)
- `docs/MQTT_GATEWAY_DALY_BLE.md` DALY BLE via MQTT (Topics + Service + Schema)
- `docs/PROTOCOL_RS485_1363.md` Reverse-Engineering / Service-Mapping (Frameformat, CRC, Layouts, Snippets)
- `docs/DEPLOY_NODE_RED.md` Backup, Restore, Restart
- `docs/INFLUXDB_GRAFANA.md` InfluxDB + Grafana (Schema + Queries)
- `docs/GRAFANA_IMPORT.md` Dashboard Import
- `node-red/flows.rs485-service.snapshot.json` Flow-Snapshot
- `scripts/backup-flows.sh` Backup-Helper
