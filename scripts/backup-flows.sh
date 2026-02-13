#!/usr/bin/env bash
set -euo pipefail
TS=$(date +%F_%H%M%S)
cp /home/black/.node-red/flows.json /home/black/.node-red/flows.json.bak_${TS}
cp /home/black/.node-red/flows_cred.json /home/black/.node-red/flows_cred.json.bak_${TS}
echo "Backup erstellt: ${TS}"
