# Grafana Import (Dashboard JSON)

Dieses Repo enthaelt ein fertiges Grafana Dashboard fuer:
- JK BLE (`rp48h.jk_ble`)
- RS485 (`rp48h.rs485_status`, `rp48h.rs485_limits`)

Datei:
- `grafana/bms-influxdb-rp48h-dashboard.json`

## Voraussetzungen
- InfluxDB laeuft (Port `8086`)
- Database `bms` existiert
- Retention Policy `rp48h` existiert
- Node-RED schreibt Daten nach InfluxDB (siehe `docs/INFLUXDB_GRAFANA.md`)

## Datasource in Grafana (InfluxQL)
In Grafana eine InfluxDB Datasource anlegen:
- Type: `InfluxDB`
- Query language: `InfluxQL`
- URL: `http://<pi-ip>:8086`
- Database: `bms`
- User/Password: leer (sofern Influx Auth nicht aktiviert wurde)

## Dashboard Import
1. Grafana: `Dashboards` -> `New` -> `Import`
2. JSON aus `grafana/bms-influxdb-rp48h-dashboard.json` einfügen
3. Beim Import die Variable `DS_INFLUX` auf deine InfluxDB Datasource mappen
4. Speichern

## Panel Beispiele (Queries)

JK Voltage:
```sql
SELECT mean("voltage") FROM "rp48h"."jk_ble" WHERE $timeFilter GROUP BY time($__interval) fill(null)
```

RS485 Voltage (per addr Variable):
```sql
SELECT mean("voltage") FROM "rp48h"."rs485_status" WHERE $timeFilter AND "addr" =~ /^$addr$/ GROUP BY time($__interval) fill(null)
```

## Hinweise
- Das Dashboard nutzt `rp48h.<measurement>`. Wenn du spaeter eine andere Retention Policy nutzt, passe die Queries an.
- Wenn keine RS485 Daten kommen, pruefe zuerst in Influx:
  - `SHOW MEASUREMENTS`
  - `SELECT * FROM rp48h.rs485_status ORDER BY time DESC LIMIT 5`

