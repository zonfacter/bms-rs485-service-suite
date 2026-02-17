# Third-Party Sources and License Notes

Dieses Repo enthaelt eigene Implementierungen fuer BLE/MQTT/Node-RED.

## Referenzprojekte (Ideen/Architektur)

- `fl4p/batmon-ha`:
  - URL: https://github.com/fl4p/batmon-ha
  - Verwendet als konzeptionelle Referenz fuer:
    - Adapter-/Device-Struktur
    - Gruppen-/Aggregat-Ansatz
    - Influx/Grafana Betriebsmuster
  - Es wurde hier **kein Code 1:1 uebernommen**.

## Lizenzhinweis

- Das Projekt `batmon-ha` verwendet MIT und LGPL-2.1 Lizenzen.
- Solange wir nur Ideen/Konzepte adaptieren und selbst neu implementieren, gibt es i.d.R. keine Copyleft-Pflichten fuer dieses Repo.
- Bei direkter Code-Uebernahme muessten die jeweiligen Lizenzbedingungen (inkl. Hinweise/Kopien) eingehalten werden.

## Praktische Konsequenz fuer dieses Repo

- Quellen werden dokumentiert.
- Eigene Implementierung bleibt bevorzugt.
- Bei spaeterer direkter Code-Uebernahme: vorher Lizenz-Check im Commit/PR.
