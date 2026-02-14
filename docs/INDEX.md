# Dokumentation (Start)

Diese Seite ist das Inhaltsverzeichnis fuer das Repo. Sie ist absichtlich:
- menschenlesbar (schnelle Orientierung)
- KI-freundlich (klare Begriffe, stabile Feldnamen, strukturierte Tabellen)

## Schnellnavigation
- Protokoll-Spezifikation (Frame + Decoder Layouts): [`PROTOCOL_RS485_1363.md`](PROTOCOL_RS485_1363.md)
- Mappings (Command IDs, Passwort, Module): [`MAPPINGS.md`](MAPPINGS.md)
- Dashboard Bedienung (Node-RED UI): [`DASHBOARD_SERVICE.md`](DASHBOARD_SERVICE.md)
- Backup/Restore/Restart (Betrieb): [`DEPLOY_NODE_RED.md`](DEPLOY_NODE_RED.md)
- JK-BMS BLE via MQTT (Gateway + Topics + Schema): [`MQTT_GATEWAY_JK_BLE.md`](MQTT_GATEWAY_JK_BLE.md)
- InfluxDB + Grafana (Timeseries Export): [`INFLUXDB_GRAFANA.md`](INFLUXDB_GRAFANA.md)
- Grafana Dashboard Import (fertiges JSON): [`GRAFANA_IMPORT.md`](GRAFANA_IMPORT.md)
- DALY Balancer BLE (Reverse Engineering Notes): [`BLE_BALANCER_DALY.md`](BLE_BALANCER_DALY.md)

## Was ist implementiert?
- Frame Builder (ASCII, CID1=4A, CID2 variabel) inkl. LEN/LCS und 16-bit ASCII-Checksum
- Decoder (dekodiert Services):
- `42` Status/Zellen/Temperaturen/U/I/Kapazitaet (robust gegen Versatz)
- `44` Alarms/Fahnen (raw)
- `47` Limits/Parameter
- `51` ASCII Geraeteinfo
- `83` Diagnose Counter
- `B0` Module `03` (ASCII Info) und `04` (Capacity/Energy)
- Write/Control:
- `49` (Set Basic Params) Subset (Cell/Pack/Charge-Limit)
- `45`/`8B` ACK generisch (Layout noch nicht voll dekodiert)
- JK BLE -> MQTT Gateway (Bluetooth/BlueZ + Mosquitto) fuer JK-BMS (Lesen, optional Trigger Read)

## Fuer Entwickler (Python/Java/C/...)
Die Kernpunkte, die du in jeder Implementierung brauchst:
- RX muss "chunked" Eingaben puffern und Frames anhand `~22` und `\\r/\\n` trennen.
- `LENID` ist in Hex-Zeichenlaenge (nicht Bytes).
- Checksum ist 2er-Komplement einer ASCII-Summe (kein CRC-Polynomial).
- Responses sind nicht immer self-describing: nutze Request/Response-Korrelation (letzter gesendeter `CID2` pro Adresse).

## AI Prompt (zum Code-Generieren)
Wenn du eine KI direkt aus diesem Repo Code generieren lassen willst, funktioniert das erfahrungsgemaess gut mit so einem Prompt:

```text
Implementiere das RS485 ASCII Protokoll aus docs/PROTOCOL_RS485_1363.md.
Ich brauche:
1) einen Frame-Builder (addr, cid2, infoHex) -> frame string mit LEN/LCS und checksum
2) einen RX-Parser der chunked input akzeptiert und valide Frames extrahiert (CRC verify)
3) Decoder fuer Services 0x42,0x44,0x47,0x51,0x83,0xB0 (03/04) gemaess Layouts
Ausgabe als JSON Objekte mit Feldnamen wie in der Spec.
Sprache: <DEINE SPRACHE>.
```
