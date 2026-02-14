#!/usr/bin/env python3
"""
BLE notification sniffer (BlueZ/bleak).

Connects to a BLE device, enables notifications on all characteristics that support notify/indicate,
and prints received payloads as JSON lines:
  {"ts":..., "char_uuid":"...", "kind":"notify", "hex":"..."}

Useful to reverse engineer devices (e.g. DALY active balancer modules) before implementing a decoder.
"""

import argparse
import asyncio
import json
import time
from typing import Any, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.bluezdbus.manager import get_global_bluez_manager


def _now() -> float:
    return time.time()


def _hex(b: bytearray | bytes) -> str:
    return bytes(b).hex()

async def _ble_device_from_bluez_cache(address: str, adapter: Optional[str]) -> Optional[BLEDevice]:
    try:
        mgr = await get_global_bluez_manager()
        await mgr.async_init()
        props = getattr(mgr, "_properties", {}) or {}
    except Exception:
        return None

    want = (address or "").strip().upper()
    if not want:
        return None

    adapter_prefix = None
    if adapter:
        a = str(adapter).strip()
        if a:
            adapter_prefix = f"/org/bluez/{a}/"

    for path, ifaces in props.items():
        try:
            dev1 = (ifaces or {}).get("org.bluez.Device1") or {}
            addr = str(dev1.get("Address") or "").strip().upper()
            if addr != want:
                continue
            if adapter_prefix and not str(path).startswith(adapter_prefix):
                continue
            name = dev1.get("Name") or dev1.get("Alias") or None
            return BLEDevice(address=want, name=name, details={"path": path, "props": dev1})
        except Exception:
            continue
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("address", help="BLE MAC/address")
    ap.add_argument("--adapter", default=None, help="BlueZ adapter name, e.g. hci1")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--duration", type=float, default=30.0, help="Sniff duration seconds")
    ap.add_argument(
        "--scan-timeout",
        type=float,
        default=10.0,
        help="Scan time to find device if not present in BlueZ cache (seconds)",
    )
    ap.add_argument("--write-uuid", default=None, help="Optional characteristic UUID to write to")
    ap.add_argument(
        "--write-hex",
        action="append",
        default=[],
        help="Hex payload to write (can be specified multiple times). Example: --write-hex aa55",
    )
    ap.add_argument(
        "--write-text",
        action="append",
        default=[],
        help="UTF-8 text payload to write (can be specified multiple times). Example: --write-text 'AT\\r\\n'",
    )
    ap.add_argument("--write-interval", type=float, default=2.0, help="Seconds between writes")
    ap.add_argument("--write-count", type=int, default=3, help="How often to repeat the write sequence")
    ap.add_argument(
        "--write-response",
        action="store_true",
        help="Use write-with-response (default is without response)",
    )
    args = ap.parse_args()

    dev = await _ble_device_from_bluez_cache(args.address, args.adapter)
    if dev is None and args.scan_timeout > 0:
        try:
            # bleak 2.x: adapter passed as kwarg; safe for older versions too
            dev = await BleakScanner.find_device_by_address(
                args.address, timeout=float(args.scan_timeout), adapter=args.adapter
            )
        except TypeError:
            dev = await BleakScanner.find_device_by_address(args.address, timeout=float(args.scan_timeout))

    client_arg: Any = dev if dev is not None else args.address

    async with BleakClient(client_arg, timeout=args.timeout, adapter=args.adapter) as client:
        out = {"ts": _now(), "connected": bool(client.is_connected), "address": args.address, "adapter": args.adapter}
        print(json.dumps(out, ensure_ascii=False))

        # snapshot readable characteristics (best-effort)
        try:
            reads = []
            for svc in client.services:
                for ch in svc.characteristics:
                    if "read" in (ch.properties or []):
                        reads.append(ch.uuid)
            # limit: only first N reads to keep connect time short
            reads = reads[:30]
            for u in reads:
                try:
                    v = await client.read_gatt_char(u)
                    print(json.dumps({"ts": _now(), "char_uuid": u, "kind": "read", "hex": _hex(v)}, ensure_ascii=False))
                except Exception as e:
                    print(
                        json.dumps({"ts": _now(), "char_uuid": u, "kind": "read_error", "error": str(e)}, ensure_ascii=False)
                    )
        except Exception:
            pass

        # enable notifications
        enabled = 0

        def mk_cb(uuid: str):
            def cb(_: int, data: bytearray):
                print(
                    json.dumps(
                        {"ts": _now(), "char_uuid": uuid, "kind": "notify", "hex": _hex(data)},
                        ensure_ascii=False,
                    )
                )

            return cb

        for svc in client.services:
            for ch in svc.characteristics:
                props = set(ch.properties or [])
                if "notify" in props or "indicate" in props:
                    try:
                        await client.start_notify(ch.uuid, mk_cb(ch.uuid))
                        enabled += 1
                        print(json.dumps({"ts": _now(), "char_uuid": ch.uuid, "kind": "notify_enabled"}, ensure_ascii=False))
                    except Exception as e:
                        print(
                            json.dumps(
                                {"ts": _now(), "char_uuid": ch.uuid, "kind": "notify_enable_error", "error": str(e)},
                                ensure_ascii=False,
                            )
                        )

        async def do_writes() -> None:
            if not args.write_uuid:
                return
            payloads: list[bytes] = []
            for hx in (args.write_hex or []):
                try:
                    payloads.append(bytes.fromhex(hx.strip().replace(" ", "")))
                except Exception:
                    print(
                        json.dumps(
                            {"ts": _now(), "kind": "write_payload_error", "error": f"bad hex: {hx}"},
                            ensure_ascii=False,
                        )
                    )
            for tx in (args.write_text or []):
                try:
                    payloads.append(str(tx).encode("utf-8"))
                except Exception:
                    pass
            if not payloads:
                return

            # repeat sequence
            for i in range(max(1, int(args.write_count))):
                for pld in payloads:
                    try:
                        await client.write_gatt_char(args.write_uuid, pld, response=bool(args.write_response))
                        print(
                            json.dumps(
                                {"ts": _now(), "kind": "write_ok", "char_uuid": args.write_uuid, "hex": pld.hex()},
                                ensure_ascii=False,
                            )
                        )
                    except Exception as e:
                        print(
                            json.dumps(
                                {"ts": _now(), "kind": "write_error", "char_uuid": args.write_uuid, "error": str(e)},
                                ensure_ascii=False,
                            )
                        )
                await asyncio.sleep(max(0.05, float(args.write_interval)))

        # kick off optional writes in background while sniffing
        wt = asyncio.create_task(do_writes())

        # wait/sniff
        await asyncio.sleep(max(0.1, args.duration))
        try:
            await wt
        except Exception:
            pass

        # stop notifications
        for svc in client.services:
            for ch in svc.characteristics:
                props = set(ch.properties or [])
                if "notify" in props or "indicate" in props:
                    try:
                        await client.stop_notify(ch.uuid)
                    except Exception:
                        pass

        print(json.dumps({"ts": _now(), "kind": "done", "notify_enabled": enabled}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
