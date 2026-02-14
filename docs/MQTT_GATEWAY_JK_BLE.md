# JK-BMS BLE -> MQTT Gateway (Raspberry Pi)

Ziel: JK-BMS per Bluetooth (BLE) auslesen und die Daten stabil per MQTT bereitstellen, damit:
- Node-RED Dashboard nicht direkt BLE/BlueZ blockiert (keine konkurrierenden Reads)
- andere Systeme (Home Assistant, ioBroker, Influx, Grafana, eigene Apps) die gleichen Daten nutzen koennen

Dieses Repo nutzt dafuer:
- `tools/jk_ble_read.py` (einmaliges Auslesen, Ausgabe immer JSON)
- `tools/jk_ble_mqtt_gateway.py` (laeuft als Dienst, pollt zyklisch und publisht per MQTT)

## Komponenten

### MQTT Broker (Mosquitto)
Auf dem Pi (localhost):
```bash
sudo apt-get update
sudo apt-get install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

Test:
```bash
mosquitto_sub -h 127.0.0.1 -t 'bms/#' -v
```

### Gateway Konfiguration
Datei: `config/jk_ble_gateway.json`

Beispiel: `config/jk_ble_gateway.example.json`

Wichtige Felder:
- `mqtt.host`, `mqtt.port`, `mqtt.base_topic`
- `poll_interval_s` (z.B. 10)
- `timeout_s` (Read-Timeout je Poll, z.B. 20)
- `scan_timeout_s` (Scan-Zeit, wenn BlueZ Cache leer ist; z.B. 5)
- `devices[]`:
  - `name`: z.B. `jk1`
  - `address`: BLE MAC, z.B. `C8:47:80:37:02:E8`
  - `adapter`: z.B. `hci1` (USB BT Dongle)

### Systemd Service
Unit im Repo: `systemd/jk-ble-mqtt-gateway.service`

Install:
```bash
sudo cp -a systemd/jk-ble-mqtt-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jk-ble-mqtt-gateway
```

Logs:
```bash
sudo journalctl -u jk-ble-mqtt-gateway -n 200 --no-pager
```

## MQTT Topics

Default Base Topic: `bms`

Device `name=jk1`:
- `bms/jk/jk1/raw` (JSON, nicht retained)
- `bms/jk/jk1/online` (`true`/`false`, retained)
- `bms/jk/jk1/meta` (retained JSON, z.B. Name/Adresse/Adapter)
- `bms/jk/jk1/cmd/read` (Publish irgendwas, triggert sofortiges Read)
- `bms/jk/jk1/cmd/config` (JSON, Runtime-Konfiguration)

Trigger:
```bash
mosquitto_pub -h 127.0.0.1 -t 'bms/jk/jk1/cmd/read' -n
```

Runtime-Config (Adapter/Pollrate/Timeouts):
```bash
mosquitto_pub -h 127.0.0.1 -t 'bms/jk/jk1/cmd/config' -m '{\"adapter\":\"hci0\",\"poll_interval_s\":10}'
```

Unterstuetzte Felder (alle optional):
- `address` (MAC)
- `adapter` (`hci0`, `hci1`, oder `null`/leer)
- `poll_interval_s`
- `timeout_s`
- `scan_timeout_s`

## Payload Schema (raw)

`bms/jk/jk1/raw` ist ein JSON Objekt:
- `connected`: bool (ob BLE Verbindung im Read geklappt hat)
- `error`: `null` oder `{type, message, ...}`
- `status.device_info`: HW/SW/Serial/Vendor/...
- `status.cell_info`: `total_voltage`, `current`, `battery_soc`, Temperaturen, Zellspannungen, Innenwiderstaende, ...
- `status.warnings`: vereinfachte Flags aus Bitmasken

Beispiel (gekürzt):
```json
{
  "address": "C8:47:80:37:02:E8",
  "adapter": "hci1",
  "connected": true,
  "model_nbr": "BK-BLE-1.0",
  "got": { "device_info": true, "cell_info": true, "settings": false },
  "status": {
    "last_update": 1771097625.51,
    "device_info": { "hw_rev": "19A", "sw_rev": "19.05", "serial_number": "503074P490" },
    "cell_info": { "total_voltage": 50.948, "current": 0.0, "battery_soc": 53, "voltages": [3.197, 3.175, "..."] },
    "warnings": { "cell_overvoltage": false, "cell_undervoltage": false }
  },
  "error": null
}
```

## Node-RED Integration (Empfehlung)

Wichtig: Node-RED sollte NICHT parallel selbst BLE pollen. Sonst kommt es zu BlueZ Fehlern wie
`Notify acquired` / `Operation already in progress`.

Empfohlenes Pattern:
1. Gateway pollt BLE und publisht MQTT.
2. Node-RED subscribed auf `bms/jk/jk1/raw` und baut daraus Dashboard/UI.
3. Optional: UI Button published auf `bms/jk/jk1/cmd/read`.

Test in Node-RED:
- `mqtt in` (Topic `bms/jk/jk1/raw`) -> `json` -> UI

## 48h Verlauf (ohne externe Datenbank)

Das Dashboard nutzt `ui_chart` mit `removeOlder=48` Stunden.
Zusaetzlich speichert Node-RED einen kleinen 48h Ringbuffer im Flow-Context (RAM), damit man die Daten auch per REST exportieren kann.

REST Endpoints:
- Latest (raw + metrics): `GET /jk/jk1/latest`
- History (letzte 48h, lightweight metrics): `GET /jk/jk1/history48h`

Hinweis:
- Das ist keine persistente Datenbank. Nach Node-RED Restart ist der RAM-Verlauf leer.
- Fuer echte Historie (Tage/Wochen) nutze InfluxDB/Prometheus o.a. und schreibe/scrape dort hinein.

## Troubleshooting

### DeviceNotFound (Scan findet MAC nicht)
Ursachen:
- BMS ist nicht im Advertising (z.B. wenn ein anderes Geraet verbunden ist)
- falscher Adapter (`hci0` vs `hci1`)
- Reichweite/Interferenzen

Checks:
```bash
bluetoothctl list
bluetoothctl info C8:47:80:37:02:E8
hciconfig -a
```

### Notify acquired / InProgress
Ursachen:
- zwei Reader gleichzeitig (Node-RED exec + Gateway)
- alte Prozesse haengen

Fix:
- nur EIN Poller aktiv lassen (Gateway)
- Node-RED BLE exec deaktivieren
- Dienst neu starten:
```bash
sudo systemctl restart jk-ble-mqtt-gateway
```
