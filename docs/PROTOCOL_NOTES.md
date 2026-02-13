# Protocol Notes (1363 / Pylontech-style)

Diese Notizen basieren auf:
- Original DR Web-Software JavaScript (Bundle-Analyse)
- Beobachteten DR-Logframes
- Laufender Node-RED-Kommunikation

## Beobachtete CID2 Services
- Read: `42`, `44`, `47`, `51`, `83`, `B0`
- Write/Control: `45`, `49`, `8B`

## Beispiele aus Mitschnitten
- `~22014A45C004010FFCAF`
- `~22014A45C004010DFCB1`
- `~22014AB0600A010103FF00FB6C`
- `~22014AB0600A010104FF00FB6B`

## Node-RED Decoder
Set-ACK wird fuer `49`, `45`, `8B` als Topic erzeugt:
- `rs485/bms/<addr>/setack`

Payload:
- `setack: true`
- `svc: '49'|'45'|'8B'`
- `ok: true`
