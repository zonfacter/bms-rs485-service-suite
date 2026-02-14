# InfluxDB + Grafana (JK BLE und RS485 Daten)

Dieses Projekt schreibt JK-BMS BLE Daten aus Node-RED in eine lokale InfluxDB, damit Grafana sie anzeigen kann.

## InfluxDB (lokal auf dem Raspberry Pi)

Install (Debian Paket, InfluxDB 1.x):
```bash
sudo apt-get update
sudo apt-get install -y influxdb influxdb-client
sudo systemctl enable --now influxdb
```

DB anlegen:
```bash
influx -execute 'CREATE DATABASE bms'
influx -execute 'SHOW DATABASES'
```

## Node-RED -> InfluxDB

Im Flow `JK BLE` wird in folgendes Measurement geschrieben:
- Measurement: `jk_ble`
- Database: `bms`
- Tags:
  - `device` (z.B. `jk1`)
  - `mac` (BLE MAC)
  - `vendor` (z.B. `JK_PB2A16S20P`)
- Fields (Beispiele):
  - `voltage`, `current`, `power`, `soc`
  - `temp1`, `temp2`, `temp_mos`
  - `delta_v`, `cell_min_v`, `cell_max_v`
  - `capacity_remain`, `capacity_nominal`, `cycle_count`

Quick Check:
```bash
influx -database bms -execute 'SHOW MEASUREMENTS'
influx -database bms -execute 'SELECT voltage,current,soc,temp1,delta_v FROM jk_ble ORDER BY time DESC LIMIT 10'
```

## Grafana

In Grafana (InfluxQL / InfluxDB 1.x Datasource):
- URL: `http://<pi-ip>:8086`
- Database: `bms`

Beispiel Query:
```sql
SELECT mean(\"voltage\") FROM \"jk_ble\" WHERE $timeFilter GROUP BY time($__interval) fill(null)
```

## Hinweis (Version)

Das Debian Paket ist InfluxDB 1.6.x. Fuer neuere InfluxDB (1.8/2.x) sind Installation/Setup anders (Bucket/Token/Flux).
Wenn du InfluxDB 2.x moechtest, sag Bescheid, dann stelle ich Node-RED auf `influxdbVersion=2.0` um und dokumentiere es passend.

