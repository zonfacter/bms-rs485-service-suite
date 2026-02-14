#!/usr/bin/env python3
import argparse
import asyncio
from bleak import BleakClient


async def main():
    ap = argparse.ArgumentParser(description="Dump BLE GATT services/characteristics for a device.")
    ap.add_argument("address", help="BLE MAC/address, e.g. 40:17:10:01:03:8A")
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args()

    async with BleakClient(args.address, timeout=args.timeout) as client:
        svcs = client.services
        print(f"connected={client.is_connected} address={args.address}")
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
