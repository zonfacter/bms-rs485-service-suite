# DALY BLE -> MQTT Gateway

Ziel: DALY Smart BMS / DALY-Balancer BLE Daten stabil per MQTT bereitstellen (ohne Node-RED direkt mit BlueZ zu belasten).

## Komponenten
- Reader: `tools/daly_ble_read.py` (A5 BLE Protokoll, Ausgabe JSON)
- Gateway: `tools/daly_ble_mqtt_gateway.py` (pollt zyklisch, publisht MQTT)
- Service: `systemd/daly-ble-mqtt-gateway.service`

## MQTT Topics

Base topic default: `bms`

Pro Device `name=<akku2>`:
- `bms/daly/<name>/raw` (JSON, nicht retained)
- `bms/daly/<name>/online` (`true`/`false`, retained)
- `bms/daly/<name>/meta` (retained)
- Trigger: `bms/daly/<name>/cmd/read`

Trigger Beispiel:
```bash
mosquitto_pub -h 127.0.0.1 -t 'bms/daly/akku2/cmd/read' -n
```

## Config

Beispiel:
- `config/daly_ble_gateway.example.json`

Lokale Config (nicht ins Git):
- `config/daly_ble_gateway.json`

Wichtige Felder:
- `devices[].address` (BLE MAC)
- `devices[].adapter` (`hci1` empfohlen)
- `poll_interval_s`

## Service Install
```bash
sudo cp -a systemd/daly-ble-mqtt-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now daly-ble-mqtt-gateway
sudo systemctl status daly-ble-mqtt-gateway --no-pager
```

Logs:
```bash
sudo journalctl -u daly-ble-mqtt-gateway -n 200 --no-pager
```

## Troubleshooting

Wenn `Operation already in progress` / `br-connection-canceled`:
- nur EIN Prozess soll gleichzeitig auf das Device zugreifen
- ggf. `sudo systemctl restart bluetooth`
- ggf. `sudo systemctl restart daly-ble-mqtt-gateway`

