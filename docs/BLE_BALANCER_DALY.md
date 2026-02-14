# DALY Balancer (BLE) Reverse Engineering Notes

Status: **Work in progress**. Wir haben die BLE GATT Struktur von mindestens einem Balancer (`Akku-2`) gefunden, aber noch keinen Daten-Frame dekodiert (es kommen ohne richtigen Request keine Notifications).

## Bekannte Devices

### Akku-2
- BLE Address: `40:17:10:01:06:C7` (random)
- Name: `Akku-2`
- Services:
  - `02f00000-0000-0000-0000-00000000fe00` (vendor specific)
    - `...ff04` props: `notify,read,write,write-without-response`
    - `...ff02` props: `notify,read`
    - `...ff01` props: `write,write-without-response`
    - `...ff05` props: `read,write,write-without-response`
    - `...ff03` props: `read`
    - `...ff00` props: `read`
  - `0000fff0-0000-1000-8000-00805f9b34fb`
    - `0000fff1` props: `notify,read`
    - `0000fff2` props: `read,write,write-without-response`
    - `0000fff3` props: `read,write,write-without-response`

Beobachtungen:
- `0000fff3` read liefert ASCII: `spsss_rx2_des` (hex `737073735f7278325f646573`)
- Bei aktivierten Notifications kamen bisher **keine** Daten ohne passenden Request.

## Tools im Repo

### GATT Dump
```bash
. .venv/bin/activate
python tools/ble_gatt_dump.py <MAC> --adapter hci1 --timeout 25 --scan-timeout 15
```

### Notification Sniffer (Raw Hex)
```bash
. .venv/bin/activate
python -u tools/ble_notify_sniff.py <MAC> --adapter hci1 --timeout 25 --scan-timeout 15 --duration 30
```

Optional: testweise Write an eine Characteristic (nur zum Reverse Engineering):
```bash
python -u tools/ble_notify_sniff.py <MAC> --adapter hci1 --duration 30 \
  --write-uuid 0000fff2-0000-1000-8000-00805f9b34fb --write-hex 3f --write-count 10 --write-interval 2
```

## Naechste Schritte
1. `Akku-1` und `Akku-3` einschalten und sicherstellen, dass kein Handy/App verbunden ist.
2. MACs sammeln (`bluetoothctl scan on`).
3. Pro Device:
   - GATT Dump
   - 30-60s Sniff mit Notifications
4. Wenn weiterhin keine Frames kommen:
   - Vermutlich ist ein Request/Handshake noetig. Dann brauchen wir:
     - Protokoll-Doku, oder
     - Mitschnitt/Observation aus der Original-App (welche Bytes werden geschrieben, und welche Notifications kommen zurueck).

