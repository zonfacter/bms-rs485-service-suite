#!/usr/bin/env python3
import argparse
import asyncio
from bleak import BleakScanner


async def main():
    ap = argparse.ArgumentParser(description="BLE scan (prints name, address, rssi)")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--verbose", action="store_true", help="print extra advertisement metadata")
    args = ap.parse_args()

    seen = {}  # addr -> dict(name, rssi)

    def cb(device, adv):
        addr = device.address
        name = (device.name or adv.local_name or "").strip()
        rssi = getattr(adv, "rssi", None)
        connectable = getattr(adv, "connectable", None)
        uuids = list(getattr(adv, "service_uuids", []) or [])
        mfg = dict(getattr(adv, "manufacturer_data", {}) or {})
        svc_data = dict(getattr(adv, "service_data", {}) or {})
        cur = seen.get(addr)
        if cur is None:
            seen[addr] = {
                "name": name,
                "rssi": rssi,
                "connectable": connectable,
                "uuids": uuids,
                "mfg": mfg,
                "svc_data": svc_data,
            }
            return
        # Keep strongest RSSI and non-empty name
        if rssi is not None and (cur["rssi"] is None or rssi > cur["rssi"]):
            cur["rssi"] = rssi
        if name and not cur["name"]:
            cur["name"] = name
        if connectable is not None:
            cur["connectable"] = connectable
        if uuids and not cur["uuids"]:
            cur["uuids"] = uuids
        if mfg and not cur["mfg"]:
            cur["mfg"] = mfg
        if svc_data and not cur["svc_data"]:
            cur["svc_data"] = svc_data

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(args.timeout)
    await scanner.stop()

    def sort_key(item):
        rssi = item[1]["rssi"]
        return rssi if rssi is not None else -9999

    for addr, meta in sorted(seen.items(), key=sort_key, reverse=True):
        if not args.verbose:
            print(f"{addr}\t{meta['rssi']}\t{meta['name']}")
            continue

        # compact manufacturer data preview
        mfg_preview = ""
        if meta["mfg"]:
            parts = []
            for k, v in meta["mfg"].items():
                b = bytes(v)
                parts.append(f"{k:04X}:{b[:8].hex()}")
            mfg_preview = ",".join(parts)

        uu = ",".join(meta["uuids"][:6])  # keep compact
        print(
            f"{addr}\t{meta['rssi']}\tconn={meta['connectable']}\tname={meta['name']}\tuuids={uu}\tmfg={mfg_preview}"
        )


if __name__ == "__main__":
    asyncio.run(main())
