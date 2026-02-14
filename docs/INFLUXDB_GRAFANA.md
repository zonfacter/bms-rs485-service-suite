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

Retention:
- Es wird zusaetzlich eine Retention Policy `rp48h` (48 Stunden) angelegt.
- Node-RED schreibt die Measurements in `rp48h`, damit die Datenbank automatisch nur ~48h Historie haelt.

```bash
influx -execute \"CREATE RETENTION POLICY rp48h ON bms DURATION 48h REPLICATION 1\"
influx -database bms -execute 'SHOW RETENTION POLICIES ON bms'
```

Im Flow `JK BLE` wird in folgendes Measurement geschrieben:
- Measurement: `jk_ble`
- Database: `bms`
- Retention Policy: `rp48h`
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
influx -database bms -execute 'SELECT voltage,current,soc,temp1,delta_v FROM rp48h.jk_ble ORDER BY time DESC LIMIT 10'
```

## RS485 -> InfluxDB

Die RS485 Decoder schreiben ebenfalls nach InfluxDB (RP `rp48h`):

- Measurement `rs485_status` (Topic `rs485/bms/<addr>/status`)
  - Tags: `addr` (z.B. `01`), `device=rs485`
  - Fields: `voltage,current,power,cell_min_v,cell_max_v,cell_delta_v,temp_min,temp_max,temp_avg,capacity_full_ah,capacity_remain_ah,cycles,soh,...`

- Measurement `rs485_limits` (Service 0x47, Topic `rs485/bms/<addr>/params` mit `payload.limits`)
  - Tags: `addr`, `device=rs485`
  - Fields: `cell_v_hi,cell_v_lo,pack_v_hi,pack_v_lo,chg_i_lim,dch_i_lim,chg_t_hi,chg_t_lo,dch_t_hi,dch_t_lo`

Quick Check:
```bash
influx -database bms -execute 'SELECT voltage,current,power FROM rp48h.rs485_status ORDER BY time DESC LIMIT 10'
influx -database bms -execute 'SELECT cell_v_hi,pack_v_hi,chg_i_lim FROM rp48h.rs485_limits ORDER BY time DESC LIMIT 10'
```

## Grafana

In Grafana (InfluxQL / InfluxDB 1.x Datasource):
- URL: `http://<pi-ip>:8086`
- Database: `bms`

Beispiel Query:
```sql
SELECT mean(\"voltage\") FROM \"rp48h\".\"jk_ble\" WHERE $timeFilter GROUP BY time($__interval) fill(null)
```

## Hinweis (Version)

Das Debian Paket ist InfluxDB 1.6.x. Fuer neuere InfluxDB (1.8/2.x) sind Installation/Setup anders (Bucket/Token/Flux).
Wenn du InfluxDB 2.x moechtest, sag Bescheid, dann stelle ich Node-RED auf `influxdbVersion=2.0` um und dokumentiere es passend.
