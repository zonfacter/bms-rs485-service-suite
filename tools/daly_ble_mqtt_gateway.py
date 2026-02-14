#!/usr/bin/env python3
"""
Daly BLE -> MQTT Gateway

Publishes one JSON payload per poll cycle to:
  {base_topic}/daly/<name>/raw
  {base_topic}/daly/<name>/online   ("true"/"false", retained)
  {base_topic}/daly/<name>/meta    (small JSON, retained)

Optional on-demand read trigger:
  Subscribe: {base_topic}/daly/<name>/cmd/read  (any payload triggers immediate read)

This gateway serializes BLE reads (per device) to avoid BlueZ concurrency issues.

Optional runtime config:
  Publish JSON to: {base_topic}/daly/<name>/cmd/config
    {"address":"..","adapter":"hci0","poll_interval_s":10,"timeout_s":20,"scan_timeout_s":10}
  The gateway applies changes in-memory and persists back to the config file.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt


def _now() -> float:
    return time.time()

def _with_ble_lock(fn, *, timeout_s: float = 30.0):
    """
    Serialize BLE operations across multiple processes (JK gateway + DALY gateway).
    BlueZ can fail with InProgress/Notify acquired when two processes use the same adapter.
    """
    import fcntl

    lock_path = os.environ.get("BMS_BLE_LOCK_PATH", "/tmp/bms_ble.lock")
    deadline = time.time() + float(timeout_s)
    with open(lock_path, "w", encoding="utf-8") as f:
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise TimeoutError("BLE lock timeout")
                time.sleep(0.1)
        return fn()


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _run_read(
    python: str, address: str, adapter: Optional[str], timeout_s: float, scan_timeout_s: float
) -> Dict[str, Any]:
    def _do():
        cmd = [
            python,
            "-u",
            os.path.join(os.path.dirname(__file__), "daly_ble_read.py"),
            "--address",
            address,
            "--timeout",
            str(timeout_s),
            "--scan-timeout",
            str(scan_timeout_s),
        ]
        if adapter:
            cmd += ["--adapter", adapter]

        return subprocess.run(cmd, capture_output=True, text=True)

    p = _with_ble_lock(_do, timeout_s=max(30.0, float(timeout_s) + float(scan_timeout_s) + 10.0))
    out = (p.stdout or "").strip()
    if not out:
        return {
            "address": address,
            "adapter": adapter,
            "connected": False,
            "got": {},
            "status": {},
            "error": {"type": "EmptyStdout", "message": "daly_ble_read.py produced no stdout", "rc": p.returncode},
        }
    try:
        return json.loads(out)
    except Exception:
        return {
            "address": address,
            "adapter": adapter,
            "connected": False,
            "got": {},
            "status": {},
            "error": {
                "type": "BadJSON",
                "message": "Failed to parse daly_ble_read.py stdout as JSON",
                "rc": p.returncode,
                "stdout_head": out[:200],
                "stderr_head": (p.stderr or "")[:200],
            },
        }


@dataclass
class DeviceCfg:
    name: str
    address: str
    adapter: Optional[str]


class Gateway:
    def __init__(self, cfg: Dict[str, Any], python: str, config_path: str) -> None:
        self.cfg = cfg
        self.python = python
        self.config_path = config_path

        m = cfg.get("mqtt") or {}
        self.mqtt_host = m.get("host", "127.0.0.1")
        self.mqtt_port = int(m.get("port", 1883))
        self.mqtt_user = m.get("username")
        self.mqtt_pass = m.get("password")
        self.base_topic = str(m.get("base_topic", "bms")).strip().strip("/")
        self.client_id = m.get("client_id") or f"daly-ble-gateway-{os.getpid()}"

        self.poll_interval_s = float(cfg.get("poll_interval_s", 10))
        self.timeout_s = float(cfg.get("timeout_s", 20))
        self.scan_timeout_s = float(cfg.get("scan_timeout_s", 10))

        self.devices: list[DeviceCfg] = []
        for d in (cfg.get("devices") or []):
            self.devices.append(
                DeviceCfg(
                    name=str(d.get("name") or d.get("address") or "daly").strip(),
                    address=str(d.get("address")).strip(),
                    adapter=(str(d.get("adapter")).strip() if d.get("adapter") else None),
                )
            )

        self._cmdq: "queue.Queue[tuple[str, str, Optional[Dict[str, Any]]]]" = queue.Queue()
        self._stop = threading.Event()

        self._client = mqtt.Client(client_id=self.client_id, clean_session=True)
        self._client.enable_logger()
        if self.mqtt_user:
            self._client.username_pw_set(self.mqtt_user, self.mqtt_pass)

        for dev in self.devices:
            self._client.will_set(self._t(dev, "online"), payload="false", retain=True, qos=1)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    def _t(self, dev: DeviceCfg, suffix: str) -> str:
        return f"{self.base_topic}/daly/{dev.name}/{suffix}".replace("//", "/")

    def _publish_json(self, topic: str, payload_obj: Any, retain: bool = False) -> None:
        self._client.publish(topic, json.dumps(payload_obj, ensure_ascii=False), qos=1, retain=retain)

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Dict[str, Any], rc: int) -> None:
        for dev in self.devices:
            client.subscribe(self._t(dev, "cmd/read"), qos=0)
            client.subscribe(self._t(dev, "cmd/config"), qos=0)
        for dev in self.devices:
            self._publish_json(
                self._t(dev, "meta"),
                {"name": dev.name, "address": dev.address, "adapter": dev.adapter, "ts": _now()},
                retain=True,
            )
            client.publish(self._t(dev, "online"), payload="false", qos=1, retain=True)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        parts = (msg.topic or "").split("/")
        if len(parts) < 5:
            return
        name = parts[-3]
        cmd = parts[-2] + "/" + parts[-1]
        if cmd == "cmd/read":
            self._cmdq.put((name, "read", None))
            return
        if cmd == "cmd/config":
            try:
                raw = msg.payload.decode("utf-8") if isinstance(msg.payload, (bytes, bytearray)) else str(msg.payload)
                cfg = json.loads(raw) if raw.strip() else {}
                if isinstance(cfg, dict):
                    self._cmdq.put((name, "config", cfg))
            except Exception:
                return

    def _save_cfg(self) -> None:
        try:
            cfg = dict(self.cfg)
            cfg["poll_interval_s"] = self.poll_interval_s
            cfg["timeout_s"] = self.timeout_s
            cfg["scan_timeout_s"] = self.scan_timeout_s
            cfg["devices"] = [{"name": d.name, "address": d.address, "adapter": d.adapter} for d in self.devices]
            tmp = self.config_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, self.config_path)
            self.cfg = cfg
        except Exception:
            return

    def connect(self) -> None:
        self._client.connect(self.mqtt_host, self.mqtt_port, keepalive=30)
        self._client.loop_start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._client.loop_stop()
        except Exception:
            pass
        try:
            self._client.disconnect()
        except Exception:
            pass

    def run(self) -> int:
        if not self.devices:
            print("No devices configured.", file=sys.stderr)
            return 2
        self.connect()
        try:
            next_poll = {d.name: 0.0 for d in self.devices}
            while not self._stop.is_set():
                now = _now()
                try:
                    while True:
                        name, action, payload = self._cmdq.get_nowait()
                        if action == "read":
                            next_poll[name] = 0.0
                        elif action == "config" and isinstance(payload, dict):
                            for dev in self.devices:
                                if dev.name != name:
                                    continue
                                if payload.get("address"):
                                    dev.address = str(payload["address"]).strip()
                                if "adapter" in payload:
                                    a = payload["adapter"]
                                    a = None if a is None or str(a).strip() == "" else str(a).strip()
                                    if a is None or (a.startswith("hci") and a[3:].isdigit()):
                                        dev.adapter = a
                                self._publish_json(
                                    self._t(dev, "meta"),
                                    {"name": dev.name, "address": dev.address, "adapter": dev.adapter, "ts": _now()},
                                    retain=True,
                                )
                                next_poll[name] = 0.0

                            if "poll_interval_s" in payload:
                                try:
                                    v = float(payload["poll_interval_s"])
                                    if v >= 1:
                                        self.poll_interval_s = v
                                except Exception:
                                    pass
                            if "timeout_s" in payload:
                                try:
                                    v = float(payload["timeout_s"])
                                    if v >= 5:
                                        self.timeout_s = v
                                except Exception:
                                    pass
                            if "scan_timeout_s" in payload:
                                try:
                                    v = float(payload["scan_timeout_s"])
                                    if v >= 0:
                                        self.scan_timeout_s = v
                                except Exception:
                                    pass

                            self._save_cfg()
                except queue.Empty:
                    pass

                did_work = False
                for dev in self.devices:
                    if now < next_poll.get(dev.name, 0.0):
                        continue
                    did_work = True
                    next_poll[dev.name] = now + self.poll_interval_s

                    payload = _run_read(
                        python=self.python,
                        address=dev.address,
                        adapter=dev.adapter,
                        timeout_s=self.timeout_s,
                        scan_timeout_s=self.scan_timeout_s,
                    )
                    ok = bool(payload.get("connected")) and not payload.get("error")
                    self._publish_json(self._t(dev, "raw"), payload, retain=False)
                    self._client.publish(self._t(dev, "online"), payload=("true" if ok else "false"), qos=1, retain=True)

                if not did_work:
                    time.sleep(0.1)
        finally:
            for dev in self.devices:
                try:
                    self._client.publish(self._t(dev, "online"), payload="false", qos=1, retain=True)
                except Exception:
                    pass
            self.close()
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    cfg = _load_json(args.config)
    gw = Gateway(cfg, python=args.python, config_path=args.config)
    return gw.run()


if __name__ == "__main__":
    raise SystemExit(main())
