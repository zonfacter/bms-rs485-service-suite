#!/usr/bin/env python3
"""
JK-BMS BLE -> MQTT Gateway

Publishes one JSON payload per poll cycle to:
  {base_topic}/jk/<name>/raw
  {base_topic}/jk/<name>/online   ("true"/"false", retained)
  {base_topic}/jk/<name>/meta    (small JSON, retained)

Optional on-demand read trigger:
  Subscribe: {base_topic}/jk/<name>/cmd/read  (any payload triggers immediate read)

The gateway avoids concurrent BLE operations by serializing reads per device.
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


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _env_default(name: str, default: Optional[str]) -> Optional[str]:
    v = os.environ.get(name)
    return default if v is None or v == "" else v


def _run_read(python: str, address: str, adapter: Optional[str], timeout_s: float, scan_timeout_s: float) -> Dict[str, Any]:
    cmd = [
        python,
        "-u",
        os.path.join(os.path.dirname(__file__), "jk_ble_read.py"),
        "--address",
        address,
        "--timeout",
        str(timeout_s),
        "--scan-timeout",
        str(scan_timeout_s),
    ]
    if adapter:
        cmd += ["--adapter", adapter]

    p = subprocess.run(cmd, capture_output=True, text=True)
    # jk_ble_read.py always prints JSON; in worst case, still provide a JSON envelope here.
    out = (p.stdout or "").strip()
    if not out:
        return {
            "address": address,
            "adapter": adapter,
            "connected": False,
            "model_nbr": None,
            "got": {},
            "status": {},
            "error": {"type": "EmptyStdout", "message": "jk_ble_read.py produced no stdout", "rc": p.returncode},
        }
    try:
        return json.loads(out)
    except Exception:
        return {
            "address": address,
            "adapter": adapter,
            "connected": False,
            "model_nbr": None,
            "got": {},
            "status": {},
            "error": {
                "type": "BadJSON",
                "message": "Failed to parse jk_ble_read.py stdout as JSON",
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
    def __init__(self, cfg: Dict[str, Any], python: str) -> None:
        self.cfg = cfg
        self.python = python

        m = cfg.get("mqtt") or {}
        self.mqtt_host = m.get("host", "127.0.0.1")
        self.mqtt_port = int(m.get("port", 1883))
        self.mqtt_user = m.get("username")
        self.mqtt_pass = m.get("password")
        self.base_topic = m.get("base_topic", "bms").strip().strip("/")
        self.client_id = m.get("client_id") or f"jk-ble-gateway-{os.getpid()}"

        self.poll_interval_s = float(cfg.get("poll_interval_s", 10))
        self.timeout_s = float(cfg.get("timeout_s", 20))
        self.scan_timeout_s = float(cfg.get("scan_timeout_s", 0))

        self.devices = []
        for d in (cfg.get("devices") or []):
            self.devices.append(
                DeviceCfg(
                    name=str(d.get("name") or d.get("address") or "jk").strip(),
                    address=str(d.get("address")).strip(),
                    adapter=(str(d.get("adapter")).strip() if d.get("adapter") else None),
                )
            )

        self._cmdq: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._stop = threading.Event()

        self._client = mqtt.Client(client_id=self.client_id, clean_session=True)
        self._client.enable_logger()
        if self.mqtt_user:
            self._client.username_pw_set(self.mqtt_user, self.mqtt_pass)

        # LWT: offline markers
        for dev in self.devices:
            self._client.will_set(self._t(dev, "online"), payload="false", retain=True, qos=1)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    def _t(self, dev: DeviceCfg, suffix: str) -> str:
        return f"{self.base_topic}/jk/{dev.name}/{suffix}".replace("//", "/")

    def _publish_json(self, topic: str, payload_obj: Any, retain: bool = False) -> None:
        self._client.publish(topic, json.dumps(payload_obj, ensure_ascii=False), qos=1, retain=retain)

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Dict[str, Any], rc: int) -> None:
        # Subscribe to on-demand read triggers
        for dev in self.devices:
            client.subscribe(self._t(dev, "cmd/read"), qos=0)

        # Publish retained meta + mark online=false until first good read
        for dev in self.devices:
            self._publish_json(
                self._t(dev, "meta"),
                {"name": dev.name, "address": dev.address, "adapter": dev.adapter, "ts": _now()},
                retain=True,
            )
            client.publish(self._t(dev, "online"), payload="false", qos=1, retain=True)

    def _on_message(self, client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        topic = msg.topic or ""
        # Expect: {base}/jk/<name>/cmd/read
        parts = topic.split("/")
        # minimum: base, jk, name, cmd, read
        if len(parts) < 5:
            return
        try:
            name = parts[-3]
            cmd = parts[-2] + "/" + parts[-1]
        except Exception:
            return
        if cmd != "cmd/read":
            return
        self._cmdq.put((name, "read"))

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

                # handle queued commands (read now)
                try:
                    while True:
                        name, action = self._cmdq.get_nowait()
                        if action == "read":
                            next_poll[name] = 0.0
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
            # Mark offline on exit
            for dev in self.devices:
                try:
                    self._client.publish(self._t(dev, "online"), payload="false", qos=1, retain=True)
                except Exception:
                    pass
            self.close()
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Path to jk_ble_gateway.json")
    ap.add_argument("--python", default=None, help="Python interpreter to use for jk_ble_read.py")
    args = ap.parse_args()

    cfg = _load_json(args.config)
    python = args.python or _env_default("JK_GATEWAY_PYTHON", sys.executable) or sys.executable

    gw = Gateway(cfg, python=python)
    return gw.run()


if __name__ == "__main__":
    raise SystemExit(main())

