#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bleak import BleakScanner
import paho.mqtt.client as mqtt


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_cfg(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _safe_write_json(path: str, obj: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def _to_hex_mfg(mfg: dict[int, bytes]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in (mfg or {}).items():
        try:
            out[f"{int(key):04X}"] = bytes(val).hex()
        except Exception:
            continue
    return out


async def run_scan(adapter: str | None, timeout_s: float, min_rssi: int) -> list[dict[str, Any]]:
    # Serialize BLE operations with the same global lock used by JK/Daly gateways.
    lock_path = os.environ.get("BMS_BLE_LOCK_PATH", "/tmp/bms_ble.lock")
    lock_timeout_s = float(os.environ.get("BMS_BLE_LOCK_TIMEOUT_S", max(30.0, float(timeout_s) + 10.0)))
    deadline = time.time() + lock_timeout_s
    lock_f = open(lock_path, "w", encoding="utf-8")
    while True:
        try:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() >= deadline:
                lock_f.close()
                raise TimeoutError("BLE lock timeout")
            await asyncio.sleep(0.1)

    seen: dict[str, dict[str, Any]] = {}

    def cb(device, adv) -> None:
        addr = str(getattr(device, "address", "") or "").upper()
        if not addr:
            return
        name = (getattr(device, "name", None) or getattr(adv, "local_name", None) or "").strip()
        rssi = getattr(adv, "rssi", None)
        connectable = getattr(adv, "connectable", None)
        uuids = list(getattr(adv, "service_uuids", []) or [])
        mfg = dict(getattr(adv, "manufacturer_data", {}) or {})

        cur = seen.get(addr)
        if cur is None:
            seen[addr] = {
                "address": addr,
                "name": name,
                "rssi": rssi,
                "connectable": connectable,
                "service_uuids": uuids,
                "manufacturer_data_hex": _to_hex_mfg(mfg),
            }
            return

        if isinstance(rssi, (int, float)) and (
            not isinstance(cur.get("rssi"), (int, float)) or rssi > cur["rssi"]
        ):
            cur["rssi"] = int(rssi)
        if name and not cur.get("name"):
            cur["name"] = name
        if connectable is not None:
            cur["connectable"] = bool(connectable)
        if uuids and not cur.get("service_uuids"):
            cur["service_uuids"] = uuids
        if mfg and not cur.get("manufacturer_data_hex"):
            cur["manufacturer_data_hex"] = _to_hex_mfg(mfg)

    try:
        try:
            scanner = BleakScanner(detection_callback=cb, adapter=(adapter or None))
        except TypeError:
            scanner = BleakScanner(detection_callback=cb)

        await scanner.start()
        await asyncio.sleep(timeout_s)
        await scanner.stop()
    finally:
        try:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_f.close()

    rows = []
    for row in seen.values():
        rssi = row.get("rssi")
        if isinstance(rssi, (int, float)) and int(rssi) < min_rssi:
            continue
        rows.append(row)

    rows.sort(key=lambda x: int(x.get("rssi") or -9999), reverse=True)
    return rows


class MqttPub:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.enabled = bool(cfg.get("enabled", True))
        self.host = str(cfg.get("host", "127.0.0.1"))
        self.port = int(cfg.get("port", 1883))
        self.topic = str(cfg.get("topic", "bms/ble/scan/latest"))
        self.cmd_topic = str(cfg.get("cmd_topic", "bms/ble/scan/cmd"))
        self.retain = bool(cfg.get("retain", True))
        self.user = cfg.get("username")
        self.password = cfg.get("password")
        self.client = mqtt.Client(client_id=str(cfg.get("client_id", "ble-scan-store")), clean_session=True)
        self.client.on_connect = self._on_connect
        if self.user:
            self.client.username_pw_set(self.user, self.password)
        self.client.on_message = self._on_message
        self.connected = False
        self.scan_now = False
        self.requested_adapter: str | None = None

    def _on_message(self, _client, _userdata, msg) -> None:
        topic = str(getattr(msg, "topic", "") or "")
        if topic != self.cmd_topic:
            return
        payload_raw = getattr(msg, "payload", b"")
        if isinstance(payload_raw, (bytes, bytearray)):
            payload_txt = payload_raw.decode("utf-8", errors="ignore").strip()
        else:
            payload_txt = str(payload_raw).strip()

        cmd = ""
        adapter: str | None = None
        try:
            if payload_txt.startswith("{"):
                obj = json.loads(payload_txt)
                if isinstance(obj, dict):
                    cmd = str(obj.get("cmd", "")).strip().lower()
                    a = obj.get("adapter")
                    if a is not None:
                        adapter = str(a).strip().lower() or None
        except Exception:
            pass

        if not cmd:
            payload = payload_txt.lower()
            # Also support text command: "scan_now hci0"
            parts = payload.split()
            if parts:
                cmd = parts[0]
                if len(parts) > 1:
                    adapter = parts[1]

        # Accept empty payload, "scan", "scan_now", "now", "1", "true".
        if cmd in {"", "scan", "scan_now", "now", "1", "true"}:
            self.scan_now = True
            if adapter in {"auto", ""}:
                adapter = None
            if adapter:
                self.requested_adapter = adapter
            print(f"{now_iso()} ble-scan cmd=scan_now topic={topic} adapter={adapter or 'unchanged'}")
            sys.stdout.flush()

    def _on_connect(self, client, _userdata, _flags, _rc) -> None:
        try:
            client.subscribe(self.cmd_topic, qos=0)
        except Exception:
            pass

    def connect(self) -> None:
        if not self.enabled:
            return
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        self.connected = True

    def publish(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.client.publish(self.topic, json.dumps(payload, ensure_ascii=False), qos=1, retain=self.retain)

    def close(self) -> None:
        if not self.enabled:
            return
        try:
            if self.connected:
                self.client.loop_stop()
                self.client.disconnect()
        except Exception:
            pass


async def run_loop(cfg_path: str) -> int:
    cfg = _read_cfg(cfg_path)
    current_adapter = (cfg.get("adapter") or "").strip() or None
    adapter_cycle_cfg = cfg.get("adapter_cycle")
    adapter_cycle: list[str] = []
    if isinstance(adapter_cycle_cfg, list):
        for a in adapter_cycle_cfg:
            s = str(a or "").strip()
            if s:
                adapter_cycle.append(s)
    cycle_idx = 0
    timeout_s = float(cfg.get("scan_timeout_s", 15.0))
    interval_s = float(cfg.get("interval_s", 60.0))
    min_rssi = int(cfg.get("min_rssi", -127))
    output_json = str(cfg.get("output_json", "/home/black/bms-rs485-service-suite/data/ble_scan_latest.json"))
    mqtt_pub = MqttPub(dict(cfg.get("mqtt") or {}))
    keep_last_nonempty = bool(cfg.get("keep_last_nonempty", True))
    last_nonempty_devices: list[dict[str, Any]] = []
    last_nonempty_ts: float | None = None

    stop = False

    def _stop(*_args) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    mqtt_pub.connect()
    try:
        while not stop:
            t0 = time.time()
            err = None
            devices: list[dict[str, Any]] = []
            if mqtt_pub.requested_adapter is not None:
                current_adapter = mqtt_pub.requested_adapter
                mqtt_pub.requested_adapter = None
            elif adapter_cycle:
                current_adapter = adapter_cycle[cycle_idx % len(adapter_cycle)]
                cycle_idx += 1
            try:
                devices = await run_scan(adapter=current_adapter, timeout_s=timeout_s, min_rssi=min_rssi)
            except Exception as e:  # pragma: no cover
                err = {"type": type(e).__name__, "message": str(e)}

            payload: dict[str, Any] = {
                "ts": time.time(),
                "ts_iso": now_iso(),
                "adapter": current_adapter,
                "scan_timeout_s": timeout_s,
                "min_rssi": min_rssi,
                "count": len(devices),
                "devices": devices,
                "error": err,
            }
            if devices:
                last_nonempty_devices = devices
                last_nonempty_ts = time.time()
                payload["using_last_nonempty"] = False
            elif keep_last_nonempty and last_nonempty_devices:
                payload["count"] = len(last_nonempty_devices)
                payload["devices"] = last_nonempty_devices
                payload["using_last_nonempty"] = True
                payload["last_nonempty_ts"] = last_nonempty_ts

            _safe_write_json(output_json, payload)
            mqtt_pub.publish(payload)
            shown_count = int(payload.get("count") or 0)
            print(
                f"{now_iso()} ble-scan count={shown_count} adapter={current_adapter or 'auto'}"
                + (f" error={err['type']}" if err else "")
            )
            sys.stdout.flush()

            if mqtt_pub.scan_now:
                mqtt_pub.scan_now = False
                continue

            elapsed = time.time() - t0
            sleep_s = max(1.0, interval_s - elapsed)
            for _ in range(int(sleep_s * 10)):
                if stop:
                    break
                if mqtt_pub.scan_now:
                    mqtt_pub.scan_now = False
                    break
                await asyncio.sleep(0.1)
    finally:
        mqtt_pub.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Periodic BLE scanner that stores JSON and publishes MQTT.")
    ap.add_argument(
        "--config",
        default="/home/black/bms-rs485-service-suite/config/ble_scan_store.json",
        help="Path to scanner config JSON",
    )
    args = ap.parse_args()
    return asyncio.run(run_loop(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
