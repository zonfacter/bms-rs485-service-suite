# RS485 ASCII Protocol (CID1=4A, CID2 Services) - Reverse Engineered

Ziel: Diese Datei beschreibt das auf dem Bus beobachtete ASCII-Frameformat sowie die aktuell dekodierten Services so, dass man Implementierungen in beliebigen Sprachen (Python/Java/C/Go/...) bauen kann.

Quelle der Fakten:
- Node-RED Decoder/Builder (laufend auf dem Pi)
- Beobachtete reale Frames aus Hersteller-Logs und Live-RS485
- Bundle-Analyse der Hersteller-Websoftware (JS)

## Transport
- Physikalisch: RS485
- UART: im Node-RED Setup wurde `/dev/ttyUSB0` mit `9600 8N1` verwendet (andere BMS koennen abweichen).
- Daten sind ASCII-Textframes, terminieren typischerweise mit `\\r` (CR) oder `\\n`.

## Frameformat (ASCII)
Frame als ASCII-String (Hex-Zeichen), Startzeichen `~`:

```
~ 22  <ADR>  4A  <CID2>  <LEN>  <INFO...>  <CRC>
^    ^   ^    ^     ^      ^       ^        ^
|    |   |    |     |      |       |        4 hex chars (16-bit)
|    |   |    |     |      |       0..n hex chars
|    |   |    |     |      4 hex chars (LCS + LENID)
|    |   |    |     2 hex chars
|    |   |    constant CID1 = 0x4A
|    |   2 hex chars (Pack-Adresse)
|    constant "22"
Start "~"
```

Hinweis: Es gibt Varianten, in denen auf Leitungsebene das Startbyte `0x7E` auftaucht. In der Node-RED Implementierung wird das als String `~` abgebildet.

### ADR
- 1 Byte als 2 Hex-Zeichen, z.B. `01`, `03`

### CID1
- in allen beobachteten Frames: `4A`

### CID2 (Service)
Beobachtet/unterstuetzt:
- Read: `42`, `44`, `47`, `51`, `83`, `B0`
- Write/Control: `45`, `49`, `8B` (ACK derzeit generisch)

### LEN (LENID + LCS)
LEN ist 16-bit, dargestellt als 4 Hexzeichen.
- Untere 12 Bit: `LENID` = Laenge des `INFO` Feldes in Hex-Zeichen (nicht Bytes).
- Obere 4 Bit: `LCS` = Check-Nibble fuer `LENID`

Berechnung (aus Node-RED):
- `sum = (LENID & 0xF) + ((LENID>>4)&0xF) + ((LENID>>8)&0xF)`
- `LCS = (((~sum) & 0xF) + 1) & 0xF`
- `LEN = (LCS<<12) | (LENID & 0x0FFF)`

### CRC (16-bit)
CRC ist hier kein CRC-Polynom, sondern eine ASCII-Checksumme:
- Summe aller ASCII-Codes ab Index 1 (also ohne `~`), bis vor die letzten 4 CRC-Hexzeichen.
- Danach 16-bit 2er-Komplement:

Pseudocode:
```
sum = 0
for each char c in frame_without_crc, starting at index 1:
  sum += ord(c)
crc = ((~sum) & 0xFFFF) + 1
crc &= 0xFFFF
```

CRC wird als 4 Hexzeichen ans Ende gehaengt.

## Request/Response Grundregel
- In vielen Antworten steht an Headerposition `RTN=00` (OK). Node-RED verwirft Frames mit RTN != 00.
- Die Antwort enthaelt nicht immer explizit, zu welchem Service sie gehoert. In Node-RED wird deshalb der "Service-Hint" aus dem zuletzt gesendeten Request genutzt (Request-Context).

## INFO Feld (Service-abhaengig)
Die folgenden Layouts sind aus dem Node-RED Builder/Decoder abgeleitet.

### Service 0x42 (Status / Cells / U/I / Kapazitaet)
Request INFO:
- `INFO = <ADR>` (nur die Adresse)

Response `DATAHEX` (nach Header/LEN, vor CRC) ist "variant"; Node-RED dekodiert robust ueber Heuristik.

Dekodierte Felder (Node-RED Output):
- `dataflag` (u8)
- `cells_mv[]` (u16, Bereich typ. 2000..4000 mV)
- `temps[]` (i16, entweder Celsius*10 oder Kelvin*10; Node-RED entscheidet heuristisch)
- `current` (i16, scale meist /100; fallback /1000)
- `voltage` (u16, scale meist /100; fallback /1000; wenn unplausibel: Summe Zellspannungen)
- `remain_capacity_ah` (u16 /100)
- `full_capacity_ah` (u16 /100)
- `cycles` (u16)
- `soh` (u16 /100, optional)

Wichtiger Hinweis:
- Einige Varianten haben Byte-/Nibble-Versatz vor dem Cell-Count. Node-RED sucht daher eine "beste" Alignment-Position (Score ueber plausible Zellspannungen).

### Service 0x44 (Alarms / Flags - raw)
Request INFO:
- `INFO = <ADR>`

Response:
- `dataflag` (u8)
- optional 1 Byte "maybeCmd" wird uebersprungen wenn `0xFF` oder `ADR`
- Rest wird als `raw` (Hexstring) gespeichert

### Service 0x47 (Limits / Parameter)
Request INFO:
- `INFO = <ADR>`

Response Layout (alle Werte 2 Byte, aber im Frame als 4 Hexchars pro Wert):
1. `dataflag` u8
2. `cell_v_hi` u16 /100
3. `cell_v_lo` u16 /100
4. `cell_v_uv` u16 /100
5. `chg_t_hi` i16 -> temp (Kelvin*10 oder Celsius*10)
6. `chg_t_lo` i16 -> temp
7. `chg_i_lim` i16 /100 (A)
8. `pack_v_hi` u16 /100
9. `pack_v_lo` u16 /100
10. `pack_v_uv` u16 /100
11. `dch_t_hi` i16 -> temp
12. `dch_t_lo` i16 -> temp
13. `dch_i_lim` i16 /100

Anmerkung:
- In UI/Doku solltest du diese Werte ggf. normalisieren (z.B. /10 oder /1000) falls die Quelle anders skaliert. Node-RED macht im UI eine Normalisierungsschicht.

### Service 0x51 (Geraeteinfo / ASCII)
Response:
- `DATAHEX` wird als ASCII dekodiert.

### Service 0x83 (Diagnostic Counters)
Request INFO (Node-RED Preset nutzt op=01):
- `INFO = <ADR><OP><DATA...optional>`

Response:
- `req_cid2` u8
- `req_op` u8
- dann mehrere Counter u16:
  - `ochg_prot_cnt`
  - `odisch_prot_cnt`
  - `oc_prot_cnt`
  - `temp_prot_cnt`
  - `short_circuit_prot_cnt`
  - `mos_h_temp_prot_cnt`
  - `env_h_temp_prot_cnt`
  - `env_l_temp_prot_cnt`

### Service 0xB0 (Module Read)
Request INFO (in Node-RED):
```
INFO = <ADR><OP><MOD><FID><FLEN><PWD?><DATA?>
```
- default OP: `02`
- MOD Beispiele:
  - `03` => ASCII Info
  - `04` => Capacity/Energy Counter
- FID: oft `FF`
- FLEN: oft `00`
- PWD: optional, als HEX-String. Wenn Passwort z.B. `666666` (ASCII), dann als HEX `363636363636`.

Response decode:
- Header: erste 12 Hexchars werden als `head` gespeichert
- `mod` wird aus `head[4..6]` gelesen (d.h. Position abhaengig vom B0-Header)
- MOD 0x03:
  - Rest als ASCII dekodiert und bereinigt
- MOD 0x04:
  - 16-bit Werte (je 4 Hexchars) in Reihenfolge:
    - remaining_ah = v0 /100
    - full_ah = v1 /100
    - design_ah = v2 /100
    - total_charge_ah = v3 /100
    - total_discharge_ah = v4 /100
    - total_charge_kwh = v5 /10
    - total_discharge_kwh = v6 /10

## Write/Control Services
Node-RED erkennt fuer `49`, `45`, `8B` aktuell nur ein generisches ACK (Topic `.../setack`). Das heisst: das Response-Payload wird als `raw` gespeichert, aber nicht feldweise interpretiert.

### Service 0x49 (Set Basic Params)
Node-RED implementiert aktuell ein praktisches Subset aus der Hersteller-Websoftware:
- `0x80` CellV High: Wert als u16 (V * 1000)
- `0x81` CellV Low: Wert als u16 (V * 1000)
- `0x84` Charge Current Limit: Wert als u16 (A * 100)
- `0x85` PackV High: Wert als u16 (V * 1000)
- `0x86` PackV Low: Wert als u16 (V * 1000)

Request INFO:
```
INFO = <ADR><commandType><valueU16>
```

### Service 0x45 (Control)
Beobachtete Frames (aus Logs):
- `~22014A45C004010FFCAF`
- `~22014A45C004010DFCB1`

Im Node-RED Preset sind `CTRL_45_0D` und `CTRL_45_0F` hinterlegt (als `op` Feld). Die genaue Semantik muss pro BMS-Version validiert werden.

### Service 0x8B (Extended Params)
In der Hersteller-Software taucht `8B` fuer erweitertes Setzen auf. Node-RED hat derzeit nur:
- Frame-Builder (Expert)
- Generic Set-ACK Erkennung

## Referenz-Implementierungen

### Python: Frame bauen
```python
def lcs_for_lenid(lenid: int) -> int:
    s = (lenid & 0xF) + ((lenid >> 4) & 0xF) + ((lenid >> 8) & 0xF)
    return (((~s) & 0xF) + 1) & 0xF

def checksum_ascii(frame_wo_crc: str) -> int:
    s = 0
    for ch in frame_wo_crc[1:]:
        s += ord(ch)
    return (((~s) & 0xFFFF) + 1) & 0xFFFF

def build_frame(addr_hex: str, cid2_hex: str, info_hex: str) -> str:
    addr = addr_hex.upper().zfill(2)[-2:]
    cid2 = cid2_hex.upper().zfill(2)[-2:]
    info = info_hex.upper()
    lenid = len(info)
    lcs = lcs_for_lenid(lenid)
    lenword = (lcs << 12) | (lenid & 0x0FFF)
    prefix = f\"~22{addr}4A{cid2}{lenword:04X}{info}\"
    crc = checksum_ascii(prefix)
    return f\"{prefix}{crc:04X}\\r\"
```

### Java: Checksum pruefen
```java
static int checksumAscii(String frameNoCrc) {
  int sum = 0;
  for (int i = 1; i < frameNoCrc.length(); i++) sum += frameNoCrc.charAt(i);
  int crc = ((~sum) & 0xFFFF) + 1;
  return crc & 0xFFFF;
}

static boolean verify(String frame) {
  String s = frame.trim().toUpperCase();
  if (s.length() < 8) return false;
  String crcHex = s.substring(s.length() - 4);
  int rx = Integer.parseInt(crcHex, 16);
  String no = s.substring(0, s.length() - 4);
  return checksumAscii(no) == rx;
}
```

## Praktische Hinweise / Pitfalls
- `LENID` ist in HEX-Zeichen gemessen. Wenn du `INFO` als Bytearray baust: erst zu HEX string konvertieren und dann `len(hex_string)` verwenden.
- Responses koennen "gechunked" kommen (Serial liefert Teilstrings). Daher: RX-Buffer und Frame-Splitter implementieren.
- Service-Erkennung: wenn Response selbst nicht eindeutig ist, nutze Request/Response-Korrelation (zuletzt gesendetes `CID2` pro Adresse).
- Temperatur: manche BMS senden Kelvin*10. Node-RED nutzt: wenn raw > 1000 => Kelvin10, sonst Celsius10.

