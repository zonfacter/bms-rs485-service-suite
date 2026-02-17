# BLE Scan Store (JSON + MQTT)

Der Scanner erfasst BLE-Devices zyklisch und publisht die letzten Ergebnisse als JSON.

## Service
- Unit: `ble-scan-store.service`
- Script: `tools/ble_scan_store.py`
- Config: `config/ble_scan_store.json`

## Funktionen
- Zyklischer BLE Scan
- HCI Rotation per `adapter_cycle` (z.B. `hci0/hci1/hci2`)
- RSSI-Erfassung pro MAC
- MQTT Publish (`bms/ble/scan/latest`)
- Trigger via MQTT (`bms/ble/scan/cmd`)
- Persistenz in `data/ble_scan_latest.json`

## Wichtige Config Felder
- `adapter`: Default-Adapter (wenn keine Rotation)
- `adapter_cycle`: Liste fuer Rotation (optional)
- `scan_timeout_s`: Scan Dauer je Zyklus
- `interval_s`: Intervall zwischen Scans
- `min_rssi`: Filter
- `mqtt.topic`: Publish Topic
- `mqtt.cmd_topic`: Command Topic

## Beispiele
Sofortscan triggern:
```bash
mosquitto_pub -h 127.0.0.1 -t bms/ble/scan/cmd -m 'scan_now'
```

Mit Adapter erzwingen:
```bash
mosquitto_pub -h 127.0.0.1 -t bms/ble/scan/cmd -m '{"cmd":"scan_now","adapter":"hci2"}'
```

Live ansehen:
```bash
mosquitto_sub -h 127.0.0.1 -t bms/ble/scan/latest -v
```

## Betrieb
```bash
sudo systemctl restart ble-scan-store
sudo journalctl -u ble-scan-store -n 100 --no-pager
```
