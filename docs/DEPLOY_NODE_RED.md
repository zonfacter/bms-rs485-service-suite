# Deploy / Backup / Restore

## Backup erstellen
```bash
cp /home/black/.node-red/flows.json /home/black/.node-red/flows.json.bak_$(date +%F_%H%M%S)
cp /home/black/.node-red/flows_cred.json /home/black/.node-red/flows_cred.json.bak_$(date +%F_%H%M%S)
```

## Snapshot ins Projekt kopieren
```bash
cp /home/black/.node-red/flows.json /home/black/bms-rs485-service-suite/node-red/flows.rs485-service.snapshot.json
```

## Node-RED neu starten
```bash
sudo systemctl restart nodered.service
systemctl is-active nodered.service
```

## Dashboard
- URL: `http://<pi-ip>:1880/ui`
- Nach Aenderungen Browser mit `Strg+F5` neu laden
