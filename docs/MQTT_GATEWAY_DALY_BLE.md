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
- `bms/daly/<name>/state` (retained: online/stale/last_ok_age/failures)
- Trigger: `bms/daly/<name>/cmd/read`
- Runtime-Config: `bms/daly/<name>/cmd/config` (JSON)

Virtuelles Gruppen-Objekt:
- `bms/daly/fleet/raw` (aggregiert ueber online Devices)
- `bms/daly/fleet/online` (retained)

Trigger Beispiel:
```bash
mosquitto_pub -h 127.0.0.1 -t 'bms/daly/akku2/cmd/read' -n
```

Runtime-Config Beispiel (Adapter wechseln, Pollrate setzen):
```bash
mosquitto_pub -h 127.0.0.1 -t 'bms/daly/akku2/cmd/config' -m '{\"adapter\":\"hci2\",\"adapter_fallbacks\":[\"hci1\",\"hci0\"],\"sample_interval_s\":10,\"publish_interval_s\":15,\"stale_after_s\":45}'
```

Unterstuetzte Felder (alle optional):
- `address` (MAC)
- `adapter` (`hci0`, `hci1`, oder `null`/leer)
- `adapter_fallbacks` (Liste, z.B. `["hci1","hci0"]`)
- `poll_interval_s`
- `sample_interval_s`
- `publish_interval_s`
- `timeout_s`
- `scan_timeout_s`
- `stale_after_s`
- `publish_on_change`
- `min_publish_interval_on_change_s`
- `keep_last_good_on_error`
- `adapter_autoswitch`
- `group_enabled`
- `group_name`
- `group_publish_interval_s`
- `group_soc_low_pct`
- `group_soc_high_pct`
- `bt_reset_on_failures` (optional, 0=aus)
- `bt_reset_cooldown_s`

## Config

Beispiel:
- `config/daly_ble_gateway.example.json`

Lokale Config (nicht ins Git):
- `config/daly_ble_gateway.json`

Wichtige Felder:
- `devices[].address` (BLE MAC)
- `devices[].adapter` (primaerer Adapter)
- `devices[].adapter_fallbacks` (Fallback-Reihenfolge)
- `sample_interval_s` (read cadence)
- `publish_interval_s` (raw publish cadence)
- `stale_after_s` (ab wann `online=false`)

## Neue Gateway-Logik

- Adapter-Fallback pro Device:
  - Wenn primaerer Adapter fehlschlaegt, werden die Fallback-Adapter getestet.
  - Optionales `adapter_autoswitch` setzt den zuletzt funktionierenden Adapter als neuen Primaeradapter.

- Stale-Handling:
  - Wenn kein gueltiger Read innerhalb `stale_after_s` kommt, wird `online=false` gesetzt.
  - `state` Topic zeigt `last_ok_age_s`, `failures`, `last_error_type`.

- Sample vs Publish:
  - `sample_interval_s`: wie oft BLE gelesen wird.
  - `publish_interval_s`: wie oft `raw` gesendet wird.
  - Bei `publish_on_change=true` wird auch vor Ablauf bei relevanter Aenderung publisht.

- Gruppen-Aggregat:
  - `bms/daly/fleet/raw` publisht Mittel-/Summenwerte der aktuell online Devices.
  - Enthaelt `soc_state` mit Hysterese (Low/High-Schwellen).

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

Wenn Device oft nicht gefunden wird:
- `adapter_fallbacks` setzen
- `stale_after_s` erhoehen (z.B. 90s)
- BLE-Scan pruefen (`ble-scan-store.service`)
