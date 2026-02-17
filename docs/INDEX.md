# Dokumentation (Start)

## Schnellnavigation
- RS485 Protokoll: [`PROTOCOL_RS485_1363.md`](PROTOCOL_RS485_1363.md)
- Mappings: [`MAPPINGS.md`](MAPPINGS.md)
- Dashboard Bedienung: [`DASHBOARD_SERVICE.md`](DASHBOARD_SERVICE.md)
- Node-RED Deploy/Backup: [`DEPLOY_NODE_RED.md`](DEPLOY_NODE_RED.md)
- JK BLE Gateway: [`MQTT_GATEWAY_JK_BLE.md`](MQTT_GATEWAY_JK_BLE.md)
- DALY BLE Gateway: [`MQTT_GATEWAY_DALY_BLE.md`](MQTT_GATEWAY_DALY_BLE.md)
- BLE Scan Store: [`BLE_SCAN_STORE.md`](BLE_SCAN_STORE.md)
- InfluxDB + Grafana: [`INFLUXDB_GRAFANA.md`](INFLUXDB_GRAFANA.md)
- Grafana Import: [`GRAFANA_IMPORT.md`](GRAFANA_IMPORT.md)
- DALY BLE Reverse Notes: [`BLE_BALANCER_DALY.md`](BLE_BALANCER_DALY.md)
- Third-Party Quellen/Lizenzen: [`THIRD_PARTY_SOURCES.md`](THIRD_PARTY_SOURCES.md)

## Implementiert (Kurz)
- RS485 Frame Builder/Decoder (Services 42/44/47/51/83/B0)
- JK BLE -> MQTT Gateway
- DALY BLE -> MQTT Gateway mit:
  - Adapter-Fallback je Device
  - `state` Topic mit Online/Stale/Fehlerzustand
  - `sample_interval_s` und `publish_interval_s`
  - Fleet-Aggregation Topic `bms/daly/fleet/raw`
- BLE Scanner mit HCI Rotation und RSSI-Store
- InfluxDB/Grafana Integration

## Hinweis
Historisch verwendete Felder wie `poll_interval_s` werden aus Kompatibilitaet weiterhin akzeptiert,
werden aber intern wie `sample_interval_s` behandelt.
