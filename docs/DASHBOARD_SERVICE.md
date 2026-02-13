# Dashboard: RS485 Service

## Service Presets
Feld `Aktion` akzeptiert u.a.:
- `GET_42` oder `GET_STATUS42`
- `GET_44` oder `GET_ALARM44`
- `GET_47` oder `GET_LIMITS47`
- `GET_51` oder `GET_ID51`
- `GET_83` oder `GET_DIAG83`
- `GET_B0_INFO`
- `GET_B0_CAP`
- `CTRL_45_0D`
- `CTRL_45_0F`

Hinweise:
- `Passwort HEX` nur wenn benoetigt (z.B. `363636363636` fuer `666666` in ASCII-HEX).
- Mit `Dry-Run` wird nur angezeigt, was gesendet wuerde.

## Profil -> BMS schreiben (CID2=49)
Schreibt die Kernparameter:
- Cell High/Low
- Pack High/Low
- Charge Current Limit

Technisch verwendete commandType:
- `0x80` CellV High
- `0x81` CellV Low
- `0x84` Charge Current Limit
- `0x85` PackV High
- `0x86` PackV Low

Empfehlung:
1. `Dry-Run` aktiv
2. Werte gegen Datenblatt pruefen
3. Erst dann senden

## Statusfelder
- `Service TX Status`: gesendete/erzeugte Frames
- `Preset`: Rueckmeldung des Preset-Builders
- `Profil Sendestatus`: Profilschreibfolge
- In `Parameter / Limits` erscheint ACK-Hinweis fuer `49/45/8B`
