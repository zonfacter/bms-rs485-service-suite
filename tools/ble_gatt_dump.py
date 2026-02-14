#!/usr/bin/env python3
import argparse
import asyncio
from typing import Any, Optional
from bleak import BleakClient
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.bluezdbus.manager import get_global_bluez_manager


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


async def main():
    ap = argparse.ArgumentParser(description="Dump BLE GATT services/characteristics for a device.")
    ap.add_argument("address", help="BLE MAC/address, e.g. 40:17:10:01:03:8A")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--adapter", default=None, help="BlueZ adapter name, e.g. hci1")
    ap.add_argument("--scan-timeout", type=float, default=10.0, help="Scan time if not in BlueZ cache")
    args = ap.parse_args()

    dev = await _ble_device_from_bluez_cache(args.address, args.adapter)
    if dev is None and args.scan_timeout > 0:
        try:
            dev = await BleakScanner.find_device_by_address(
                args.address, timeout=float(args.scan_timeout), adapter=args.adapter
            )
        except TypeError:
            dev = await BleakScanner.find_device_by_address(args.address, timeout=float(args.scan_timeout))

    client_arg: Any = dev if dev is not None else args.address

    async with BleakClient(client_arg, timeout=args.timeout, adapter=args.adapter) as client:
        svcs = client.services
        print(f"connected={client.is_connected} address={args.address} adapter={args.adapter}")
        for s in svcs:
            # Bleak's BlueZ backend doesn't expose stable start/end handles across versions.
            print(f"\n[SVC] {s.uuid}")
            for c in s.characteristics:
                props = ",".join(sorted(c.properties))
                # 'handle' is not always available on every backend/version; keep output portable.
                h = getattr(c, "handle", None)
                htxt = f" handle={h}" if h is not None else ""
                print(f"  [CHR] {c.uuid}{htxt}  props={props}")
                for d in c.descriptors:
                    dh = getattr(d, "handle", None)
                    dhtxt = f" handle={dh}" if dh is not None else ""
                    print(f"    [DSC] {d.uuid}{dhtxt}")


if __name__ == "__main__":
    asyncio.run(main())
