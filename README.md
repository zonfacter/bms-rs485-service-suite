# BMS RS485 Service Suite (Raspberry Pi + Node-RED)

Node-RED/Service-Suite fuer:
- RS485 BMS (Deye/Pylontech kompatibel)
- JK BLE via MQTT Gateway
- DALY BLE via MQTT Gateway
- InfluxDB + Grafana

## Highlights
- DALY Gateway mit Adapter-Fallback (`hci2 -> hci1 -> hci0`), Stale/Online State, Sample-/Publish-Trennung
- DALY Fleet Aggregation (`bms/daly/fleet/raw`) inkl. SoC-Hysterese
- BLE Scanner Store mit HCI-Rotation und RSSI-Auswertung
- InfluxDB Export fuer JK, DALY, RS485 (inkl. Zellspannungen)
- Grafana Dashboard Import JSON enthalten

## Schnellstart
1. Node-RED UI: `http://<pi-ip>:1880/ui`
2. DALY/JK Services starten (`systemctl status ...`)
3. MQTT pruefen (`mosquitto_sub -t 'bms/#' -v`)
4. Grafana Dashboard importieren (`grafana/bms-influxdb-rp48h-dashboard.json`)

## Dokumentation
- Einstieg: `docs/INDEX.md`
- DALY Gateway: `docs/MQTT_GATEWAY_DALY_BLE.md`
- BLE Scan Store: `docs/BLE_SCAN_STORE.md`
- Influx/Grafana: `docs/INFLUXDB_GRAFANA.md`
- Third-Party/Lizenzen: `docs/THIRD_PARTY_SOURCES.md`
- Changelog: `CHANGELOG.md`

## Versionierung
- SemVer Tags (`vMAJOR.MINOR.PATCH`)
- Aktuelle Version: siehe `VERSION`
