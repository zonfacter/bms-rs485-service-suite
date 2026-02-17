#!/usr/bin/env python3
"""
Daly BLE -> MQTT Gateway

Publishes per-device payloads to:
  {base_topic}/daly/<name>/raw
  {base_topic}/daly/<name>/online   (retained true/false)
  {base_topic}/daly/<name>/meta     (retained JSON)
  {base_topic}/daly/<name>/state    (retained JSON online/stale/ages)

Optional trigger/config:
  {base_topic}/daly/<name>/cmd/read
  {base_topic}/daly/<name>/cmd/config

This gateway serializes BLE reads across processes via /tmp/bms_ble.lock.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt


def _now() -> float:
    return time.time()


def _with_ble_lock(fn, *, timeout_s: float = 30.0):
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


def _is_hci(s: Optional[str]) -> bool:
    if not s:
        return False
    return s.startswith("hci") and s[3:].isdigit()


def _norm_adapter(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _uniq(seq: list[Optional[str]]) -> list[Optional[str]]:
    out: list[Optional[str]] = []
    seen: set[str] = set()
    for x in seq:
        k = "" if x is None else str(x)
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def _default_fallbacks(primary: Optional[str]) -> list[str]:
    base = ["hci2", "hci1", "hci0"]
    p = _norm_adapter(primary)
    return [x for x in base if x != p]


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


def _extract_metrics(payload: Dict[str, Any]) -> Dict[str, Optional[float]]:
    st = payload.get("status") if isinstance(payload, dict) else {}
    st = st if isinstance(st, dict) else {}
    p90 = st.get("pack_90") if isinstance(st.get("pack_90"), dict) else {}
    mm91 = st.get("cell_minmax_91") if isinstance(st.get("cell_minmax_91"), dict) else {}
    t92 = st.get("temp_minmax_92") if isinstance(st.get("temp_minmax_92"), dict) else {}
    t96 = st.get("temps_96") if isinstance(st.get("temps_96"), dict) else {}

    temp: Optional[float] = None
    temps = t96.get("temps_c") if isinstance(t96.get("temps_c"), list) else []
    if temps:
        try:
            temp = float(temps[0])
        except Exception:
            temp = None
    if temp is None and "temp_max_c" in t92:
        try:
            temp = float(t92.get("temp_max_c"))
        except Exception:
            temp = None

    def _f(x: Any) -> Optional[float]:
        try:
            if x is None:
                return None
            return float(x)
        except Exception:
            return None

    return {
        "voltage": _f(p90.get("voltage_total_v")),
        "current": _f(p90.get("current_a")),
        "soc": _f(p90.get("soc_pct")),
        "temp": temp,
        "cell_delta_v": _f(mm91.get("cell_delta_v")),
    }


def _soc_hyst(prev: str, soc: Optional[float], low: float, high: float) -> str:
    if soc is None:
        return prev
    if prev == "high" and soc > low:
        return "high"
    if prev == "low" and soc < high:
        return "low"
    return "high" if soc >= high else "low"


@dataclass
class DeviceCfg:
    name: str
    address: str
    adapter: Optional[str]
    adapter_fallbacks: list[str] = field(default_factory=list)


@dataclass
class DeviceState:
    last_sample_ts: float = 0.0
    last_publish_ts: float = 0.0
    last_ok_ts: float = 0.0
    last_payload: Optional[Dict[str, Any]] = None
    last_ok_payload: Optional[Dict[str, Any]] = None
    last_change_key: Optional[tuple] = None
    failures: int = 0
    last_adapter: Optional[str] = None
    last_error_type: Optional[str] = None
    last_bt_reset_ts: float = 0.0
    soc_state: str = "low"


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

        self.sample_interval_s = float(cfg.get("sample_interval_s", cfg.get("poll_interval_s", 10)))
        self.publish_interval_s = float(cfg.get("publish_interval_s", self.sample_interval_s))
        self.timeout_s = float(cfg.get("timeout_s", 20))
        self.scan_timeout_s = float(cfg.get("scan_timeout_s", 10))
        self.stale_after_s = float(cfg.get("stale_after_s", max(60.0, self.sample_interval_s * 3.0)))
        self.publish_on_change = bool(cfg.get("publish_on_change", True))
        self.min_publish_interval_on_change_s = float(cfg.get("min_publish_interval_on_change_s", 2.0))
        self.keep_last_good_on_error = bool(cfg.get("keep_last_good_on_error", True))
        self.adapter_autoswitch = bool(cfg.get("adapter_autoswitch", True))

        self.bt_reset_on_failures = int(cfg.get("bt_reset_on_failures", 0))
        self.bt_reset_cooldown_s = float(cfg.get("bt_reset_cooldown_s", 300.0))

        self.group_enabled = bool(cfg.get("group_enabled", True))
        self.group_name = str(cfg.get("group_name", "fleet")).strip() or "fleet"
        self.group_publish_interval_s = float(cfg.get("group_publish_interval_s", self.publish_interval_s))
        self.group_soc_low_pct = float(cfg.get("group_soc_low_pct", 70.0))
        self.group_soc_high_pct = float(cfg.get("group_soc_high_pct", 80.0))

        self.devices: list[DeviceCfg] = []
        for d in (cfg.get("devices") or []):
            primary = _norm_adapter(d.get("adapter"))
            raw_fb = d.get("adapter_fallbacks")
            fbs: list[str] = []
            if isinstance(raw_fb, list):
                for x in raw_fb:
                    s = _norm_adapter(x)
                    if _is_hci(s):
                        fbs.append(s)  # type: ignore[arg-type]
            if not fbs:
                fbs = _default_fallbacks(primary)
            self.devices.append(
                DeviceCfg(
                    name=str(d.get("name") or d.get("address") or "daly").strip(),
                    address=str(d.get("address")).strip(),
                    adapter=(primary if _is_hci(primary) else None),
                    adapter_fallbacks=[x for x in fbs if _is_hci(x)],
                )
            )

        self._cmdq: "queue.Queue[tuple[str, str, Optional[Dict[str, Any]]]]" = queue.Queue()
        self._stop = threading.Event()
        self._states: dict[str, DeviceState] = {d.name: DeviceState() for d in self.devices}
        self._force_publish: dict[str, bool] = {d.name: False for d in self.devices}
        self._group_soc_state: str = "low"

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

    def _group_t(self, suffix: str) -> str:
        return f"{self.base_topic}/daly/{self.group_name}/{suffix}".replace("//", "/")

    def _publish_json(self, topic: str, payload_obj: Any, retain: bool = False) -> None:
        self._client.publish(topic, json.dumps(payload_obj, ensure_ascii=False), qos=1, retain=retain)

    def _state_payload(self, dev: DeviceCfg, st: DeviceState, now: float) -> Dict[str, Any]:
        age = (now - st.last_ok_ts) if st.last_ok_ts > 0 else None
        stale = (age is None) or (age > self.stale_after_s)
        return {
            "name": dev.name,
            "address": dev.address,
            "adapter": dev.adapter,
            "last_adapter": st.last_adapter,
            "online": (not stale) and (st.last_ok_ts > 0),
            "stale": stale,
            "stale_after_s": self.stale_after_s,
            "last_ok_ts": st.last_ok_ts if st.last_ok_ts > 0 else None,
            "last_ok_age_s": (round(age, 1) if age is not None else None),
            "last_error_type": st.last_error_type,
            "failures": st.failures,
            "soc_state": st.soc_state,
            "ts": now,
        }

    def _change_key(self, payload: Dict[str, Any], state_payload: Dict[str, Any]) -> tuple:
        m = _extract_metrics(payload)
        err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        return (
            bool(payload.get("connected")),
            state_payload.get("online"),
            state_payload.get("stale"),
            err.get("type"),
            round(m["voltage"], 2) if m["voltage"] is not None else None,
            round(m["current"], 2) if m["current"] is not None else None,
            round(m["temp"], 1) if m["temp"] is not None else None,
            round(m["soc"], 1) if m["soc"] is not None else None,
            round(m["cell_delta_v"], 3) if m["cell_delta_v"] is not None else None,
            state_payload.get("last_adapter"),
        )

    def _try_reset_adapter(self, adapter: Optional[str]) -> None:
        if not _is_hci(adapter):
            return
        try:
            subprocess.run(["hciconfig", str(adapter), "reset"], capture_output=True, text=True, timeout=8)
        except Exception:
            pass

    def _read_with_fallback(self, dev: DeviceCfg) -> tuple[Dict[str, Any], bool, Optional[str], list[Optional[str]]]:
        adapters = _uniq([dev.adapter] + [a for a in dev.adapter_fallbacks if _is_hci(a)])
        if not adapters:
            adapters = [None]

        last_payload: Optional[Dict[str, Any]] = None
        used_adapter: Optional[str] = None
        for ad in adapters:
            p = _run_read(
                python=self.python,
                address=dev.address,
                adapter=ad,
                timeout_s=self.timeout_s,
                scan_timeout_s=self.scan_timeout_s,
            )
            last_payload = p
            ok = bool(p.get("connected")) and not p.get("error")
            used_adapter = _norm_adapter(ad)
            if ok:
                p["adapter"] = used_adapter
                return p, True, used_adapter, adapters

        if last_payload is None:
            last_payload = {
                "address": dev.address,
                "adapter": dev.adapter,
                "connected": False,
                "got": {},
                "status": {},
                "error": {"type": "NoAttempt", "message": "no adapter attempts"},
            }
        if used_adapter is not None:
            last_payload["adapter"] = used_adapter
        return last_payload, False, used_adapter, adapters

    def _save_cfg(self) -> None:
        try:
            cfg = dict(self.cfg)
            cfg["poll_interval_s"] = self.sample_interval_s
            cfg["sample_interval_s"] = self.sample_interval_s
            cfg["publish_interval_s"] = self.publish_interval_s
            cfg["timeout_s"] = self.timeout_s
            cfg["scan_timeout_s"] = self.scan_timeout_s
            cfg["stale_after_s"] = self.stale_after_s
            cfg["publish_on_change"] = self.publish_on_change
            cfg["min_publish_interval_on_change_s"] = self.min_publish_interval_on_change_s
            cfg["keep_last_good_on_error"] = self.keep_last_good_on_error
            cfg["adapter_autoswitch"] = self.adapter_autoswitch
            cfg["bt_reset_on_failures"] = self.bt_reset_on_failures
            cfg["bt_reset_cooldown_s"] = self.bt_reset_cooldown_s
            cfg["group_enabled"] = self.group_enabled
            cfg["group_name"] = self.group_name
            cfg["group_publish_interval_s"] = self.group_publish_interval_s
            cfg["group_soc_low_pct"] = self.group_soc_low_pct
            cfg["group_soc_high_pct"] = self.group_soc_high_pct
            cfg["devices"] = [
                {
                    "name": d.name,
                    "address": d.address,
                    "adapter": d.adapter,
                    "adapter_fallbacks": d.adapter_fallbacks,
                }
                for d in self.devices
            ]
            tmp = self.config_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, self.config_path)
            self.cfg = cfg
        except Exception:
            return

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: Dict[str, Any], rc: int) -> None:
        for dev in self.devices:
            client.subscribe(self._t(dev, "cmd/read"), qos=0)
            client.subscribe(self._t(dev, "cmd/config"), qos=0)

        for dev in self.devices:
            self._publish_json(
                self._t(dev, "meta"),
                {
                    "name": dev.name,
                    "address": dev.address,
                    "adapter": dev.adapter,
                    "adapter_fallbacks": dev.adapter_fallbacks,
                    "sample_interval_s": self.sample_interval_s,
                    "publish_interval_s": self.publish_interval_s,
                    "stale_after_s": self.stale_after_s,
                    "ts": _now(),
                },
                retain=True,
            )
            client.publish(self._t(dev, "online"), payload="false", qos=1, retain=True)
            self._publish_json(self._t(dev, "state"), self._state_payload(dev, self._states[dev.name], _now()), retain=True)

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

    def _apply_runtime_config(self, name: str, payload: Dict[str, Any]) -> None:
        for dev in self.devices:
            if dev.name != name:
                continue
            if payload.get("address"):
                dev.address = str(payload["address"]).strip()
            if "adapter" in payload:
                a = _norm_adapter(payload.get("adapter"))
                if a is None or _is_hci(a):
                    dev.adapter = a
            if "adapter_fallbacks" in payload and isinstance(payload.get("adapter_fallbacks"), list):
                fbs: list[str] = []
                for x in payload.get("adapter_fallbacks"):
                    s = _norm_adapter(x)
                    if _is_hci(s):
                        fbs.append(str(s))
                dev.adapter_fallbacks = fbs

            self._publish_json(
                self._t(dev, "meta"),
                {
                    "name": dev.name,
                    "address": dev.address,
                    "adapter": dev.adapter,
                    "adapter_fallbacks": dev.adapter_fallbacks,
                    "sample_interval_s": self.sample_interval_s,
                    "publish_interval_s": self.publish_interval_s,
                    "stale_after_s": self.stale_after_s,
                    "ts": _now(),
                },
                retain=True,
            )
            self._force_publish[name] = True

        if "poll_interval_s" in payload:
            try:
                v = float(payload["poll_interval_s"])
                if v >= 1:
                    self.sample_interval_s = v
            except Exception:
                pass
        if "sample_interval_s" in payload:
            try:
                v = float(payload["sample_interval_s"])
                if v >= 1:
                    self.sample_interval_s = v
            except Exception:
                pass
        if "publish_interval_s" in payload:
            try:
                v = float(payload["publish_interval_s"])
                if v >= 1:
                    self.publish_interval_s = v
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
        if "stale_after_s" in payload:
            try:
                v = float(payload["stale_after_s"])
                if v >= 5:
                    self.stale_after_s = v
            except Exception:
                pass

        for k, attr, t in [
            ("publish_on_change", "publish_on_change", bool),
            ("keep_last_good_on_error", "keep_last_good_on_error", bool),
            ("adapter_autoswitch", "adapter_autoswitch", bool),
            ("group_enabled", "group_enabled", bool),
        ]:
            if k in payload:
                try:
                    setattr(self, attr, t(payload[k]))
                except Exception:
                    pass

        for k, attr in [
            ("min_publish_interval_on_change_s", "min_publish_interval_on_change_s"),
            ("group_publish_interval_s", "group_publish_interval_s"),
            ("group_soc_low_pct", "group_soc_low_pct"),
            ("group_soc_high_pct", "group_soc_high_pct"),
            ("bt_reset_cooldown_s", "bt_reset_cooldown_s"),
        ]:
            if k in payload:
                try:
                    v = float(payload[k])
                    setattr(self, attr, v)
                except Exception:
                    pass
        if "bt_reset_on_failures" in payload:
            try:
                self.bt_reset_on_failures = int(payload["bt_reset_on_failures"])
            except Exception:
                pass

        if "group_name" in payload:
            try:
                gn = str(payload["group_name"]).strip()
                if gn:
                    self.group_name = gn
            except Exception:
                pass

        self._save_cfg()

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

    def _publish_group(self, now: float) -> None:
        if not self.group_enabled:
            return

        members: list[Dict[str, Any]] = []
        active_payloads: list[Dict[str, Any]] = []
        soc_vals: list[float] = []
        for dev in self.devices:
            st = self._states[dev.name]
            s = self._state_payload(dev, st, now)
            members.append(s)
            if s["online"] and st.last_ok_payload is not None:
                active_payloads.append(st.last_ok_payload)

        for p in active_payloads:
            m = _extract_metrics(p)
            if m["soc"] is not None:
                soc_vals.append(float(m["soc"]))

        online_count = sum(1 for m in members if m.get("online"))
        stale_count = sum(1 for m in members if m.get("stale"))

        v_vals: list[float] = []
        i_vals: list[float] = []
        p_vals: list[float] = []
        t_vals: list[float] = []
        d_vals: list[float] = []
        for p in active_payloads:
            m = _extract_metrics(p)
            if m["voltage"] is not None:
                v_vals.append(float(m["voltage"]))
            if m["current"] is not None:
                i_vals.append(float(m["current"]))
            if m["voltage"] is not None and m["current"] is not None:
                p_vals.append(float(m["voltage"]) * float(m["current"]))
            if m["temp"] is not None:
                t_vals.append(float(m["temp"]))
            if m["cell_delta_v"] is not None:
                d_vals.append(float(m["cell_delta_v"]))

        soc_avg = (sum(soc_vals) / len(soc_vals)) if soc_vals else None
        self._group_soc_state = _soc_hyst(self._group_soc_state, soc_avg, self.group_soc_low_pct, self.group_soc_high_pct)

        payload = {
            "ts": now,
            "group": self.group_name,
            "online": online_count > 0,
            "online_count": online_count,
            "stale_count": stale_count,
            "member_count": len(members),
            "members": members,
            "status": {
                "voltage_mean_v": (round(sum(v_vals) / len(v_vals), 3) if v_vals else None),
                "current_sum_a": (round(sum(i_vals), 3) if i_vals else None),
                "power_sum_w": (round(sum(p_vals), 3) if p_vals else None),
                "temp_mean_c": (round(sum(t_vals) / len(t_vals), 2) if t_vals else None),
                "cell_delta_max_v": (round(max(d_vals), 3) if d_vals else None),
                "soc_mean_pct": (round(soc_avg, 2) if soc_avg is not None else None),
                "soc_state": self._group_soc_state,
                "soc_hysteresis": {"low_pct": self.group_soc_low_pct, "high_pct": self.group_soc_high_pct},
            },
            "error": None,
        }
        self._publish_json(self._group_t("raw"), payload, retain=False)
        self._client.publish(self._group_t("online"), payload=("true" if online_count > 0 else "false"), qos=1, retain=True)

    def run(self) -> int:
        if not self.devices:
            print("No devices configured.", file=sys.stderr)
            return 2

        self.connect()
        try:
            next_sample = {d.name: 0.0 for d in self.devices}
            next_group_pub = 0.0

            while not self._stop.is_set():
                now = _now()

                try:
                    while True:
                        name, action, payload = self._cmdq.get_nowait()
                        if action == "read":
                            next_sample[name] = 0.0
                            self._force_publish[name] = True
                        elif action == "config" and isinstance(payload, dict):
                            self._apply_runtime_config(name, payload)
                            next_sample[name] = 0.0
                            self._force_publish[name] = True
                except queue.Empty:
                    pass

                did_work = False
                for dev in self.devices:
                    if now < next_sample.get(dev.name, 0.0):
                        continue
                    did_work = True
                    next_sample[dev.name] = now + self.sample_interval_s

                    st = self._states[dev.name]
                    payload_raw, ok, used_adapter, attempted = self._read_with_fallback(dev)
                    st.last_sample_ts = now
                    st.last_adapter = used_adapter
                    payload = copy.deepcopy(payload_raw)

                    if ok:
                        st.last_ok_ts = now
                        st.last_ok_payload = copy.deepcopy(payload)
                        st.failures = 0
                        st.last_error_type = None
                        if self.adapter_autoswitch and used_adapter and used_adapter != dev.adapter:
                            dev.adapter = used_adapter
                            self._save_cfg()
                    else:
                        st.failures += 1
                        err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                        st.last_error_type = str(err.get("type") or "UnknownError")
                        if self.keep_last_good_on_error and st.last_ok_payload is not None:
                            cached = copy.deepcopy(st.last_ok_payload)
                            cached["connected"] = False
                            cached["error"] = payload.get("error")
                            cached["status_cached"] = True
                            payload = cached

                        if self.bt_reset_on_failures > 0 and st.failures >= self.bt_reset_on_failures:
                            if (now - st.last_bt_reset_ts) >= self.bt_reset_cooldown_s:
                                for ad in attempted:
                                    self._try_reset_adapter(ad)
                                st.last_bt_reset_ts = now
                                st.failures = 0

                    age = (now - st.last_ok_ts) if st.last_ok_ts > 0 else None
                    stale = (age is None) or (age > self.stale_after_s)
                    online = (not stale) and (st.last_ok_ts > 0)

                    m = _extract_metrics(payload)
                    st.soc_state = _soc_hyst(st.soc_state, m.get("soc"), low=70.0, high=80.0)

                    payload["gateway"] = {
                        "name": dev.name,
                        "address": dev.address,
                        "used_adapter": used_adapter,
                        "configured_adapter": dev.adapter,
                        "attempted_adapters": [a for a in attempted if a is not None],
                        "sample_interval_s": self.sample_interval_s,
                        "publish_interval_s": self.publish_interval_s,
                        "stale_after_s": self.stale_after_s,
                        "sample_ts": now,
                        "sample_ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                    }
                    payload["stale"] = stale
                    payload["stale_age_s"] = (round(age, 1) if age is not None else None)
                    payload["online"] = online
                    payload["soc_state"] = st.soc_state

                    state_payload = self._state_payload(dev, st, now)
                    change_key = self._change_key(payload, state_payload)

                    must_publish = False
                    if self._force_publish.get(dev.name, False):
                        must_publish = True
                    elif st.last_publish_ts <= 0:
                        must_publish = True
                    elif (now - st.last_publish_ts) >= self.publish_interval_s:
                        must_publish = True
                    elif self.publish_on_change and (st.last_change_key != change_key):
                        if (now - st.last_publish_ts) >= self.min_publish_interval_on_change_s:
                            must_publish = True

                    self._publish_json(self._t(dev, "state"), state_payload, retain=True)
                    self._client.publish(self._t(dev, "online"), payload=("true" if online else "false"), qos=1, retain=True)

                    if must_publish:
                        self._publish_json(self._t(dev, "raw"), payload, retain=False)
                        st.last_publish_ts = now
                        st.last_payload = copy.deepcopy(payload)
                        st.last_change_key = change_key

                    self._force_publish[dev.name] = False

                if self.group_enabled and now >= next_group_pub:
                    self._publish_group(now)
                    next_group_pub = now + self.group_publish_interval_s

                if not did_work:
                    time.sleep(0.1)
        finally:
            for dev in self.devices:
                try:
                    self._client.publish(self._t(dev, "online"), payload="false", qos=1, retain=True)
                except Exception:
                    pass
            if self.group_enabled:
                try:
                    self._client.publish(self._group_t("online"), payload="false", qos=1, retain=True)
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
