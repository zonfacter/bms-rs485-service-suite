# Mappings (uebernommen aus Node-RED / Hersteller-UI Beobachtungen)

## CID2=49 (Set Basic Params) - commandType Subset
- `0x80` CellV High (V * 1000 -> u16)
- `0x81` CellV Low (V * 1000 -> u16)
- `0x84` Charge Current Limit (A * 100 -> u16)
- `0x85` PackV High (V * 1000 -> u16)
- `0x86` PackV Low (V * 1000 -> u16)

## CID2=B0 Module Beispiele
- MOD `0x03`: ASCII Info
- MOD `0x04`: Kapazitaet/Energie-Zaehler (siehe `PROTOCOL_RS485_1363.md`)

## Passwort
Wenn ein Passwort als ASCII bekannt ist (z.B. `666666`), kann es als HEX-ASCII in `PWD` eingesetzt werden:
- `666666` -> `363636363636`

