# InfluxDB + Grafana (JK BLE, DALY BLE, RS485)

## InfluxDB
Install (InfluxDB 1.x):
```bash
sudo apt-get update
sudo apt-get install -y influxdb influxdb-client
sudo systemctl enable --now influxdb
```

DB:
```bash
influx -execute 'CREATE DATABASE bms'
influx -execute 'SHOW DATABASES'
```

## Retention (Wichtig)
Dieses Projekt nutzt aus Kompatibilitaetsgruenden den RP-Namen `rp48h`, aber mit **unbegrenzter Laufzeit**.
Damit werden Daten nicht nach 48h geloescht.

```bash
influx -execute "CREATE RETENTION POLICY rp48h ON bms DURATION INF REPLICATION 1 DEFAULT"
influx -database bms -execute 'SHOW RETENTION POLICIES ON bms'
```

Wenn bereits `48h` gesetzt war:
```bash
influx -execute "ALTER RETENTION POLICY rp48h ON bms DURATION INF REPLICATION 1"
```

## Node-RED -> Influx Measurements

### JK BLE
- Measurement: `jk_ble`
- Tags: `device`, `mac`, `vendor`
- Fields: `voltage,current,power,soc,temp1,temp2,temp_mos,delta_v,...`

### DALY BLE
- Measurement: `daly_ble`
- Tags: `device`, `mac`, `src=daly_ble`
- Fields: `voltage,current,temp,soc,cell_min_v,cell_max_v,cell_delta_v,online,stale`

- Measurement: `daly_ble_cells`
- Tags: `device`, `mac`, `src=daly_ble`
- Fields: `cell01..cell16`

Hinweis:
- DALY Influx-Writes sind reduziert (Change-Driven + periodischer Heartbeat), um Schreiblast zu senken.

### RS485
- Measurement: `rs485_status`
- Measurement: `rs485_limits`

## Quick Checks
```bash
influx -database bms -execute 'SHOW MEASUREMENTS'
influx -database bms -execute 'SELECT voltage,current,temp,online,stale FROM rp48h.daly_ble ORDER BY time DESC LIMIT 10'
influx -database bms -execute 'SELECT cell01,cell02,cell16 FROM rp48h.daly_ble_cells ORDER BY time DESC LIMIT 3'
```

## Grafana
Datasource (InfluxQL / InfluxDB 1.x):
- URL: `http://<pi-ip>:8086`
- Database: `bms`

Beispiel:
```sql
SELECT mean("voltage") FROM "rp48h"."daly_ble" WHERE $timeFilter AND "device" =~ /^$daly_device$/ GROUP BY time($__interval) fill(null)
```
