#!/usr/bin/env python3
"""
JK-BMS BLE write operations.

Implemented:
- Set OVP / OVPR (cell) via registers 0x04 / 0x05 (as in dbus-serialbattery)
- SOC reset trick (temporarily lower OVP/OVPR)
- Experimental: toggle "control buttons" registers (0x1D/0x1E/0x1F/0x40) with 01.. or 00..

WARNING:
- Writes can permanently change BMS behavior.
- Start with --dry-run and verify values.
"""

import argparse
import asyncio
import json
import time

from bleak import BleakClient, exc

CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
CHAR_HANDLE_FAILOVER = 4

JK_REGISTER_OVP = 0x04
JK_REGISTER_OVPR = 0x05


def crc_simple(arr: bytearray, length: int) -> int:
    c = 0
    for a in arr[:length]:
        c += a
    return c.to_bytes(2, "little")[0]


def jk_float_to_hex_little(val: float) -> bytearray:
    intval = int(round(val * 1000))
    intval = max(0, min(0xFFFFFFFF, intval))
    hexval = f"{intval:0>8X}"
    return bytearray.fromhex(hexval)[::-1]


def build_write_frame(reg: int, vals4: bytearray, length: int) -> bytearray:
    frame = bytearray(20)
    frame[0] = 0xAA
    frame[1] = 0x55
    frame[2] = 0x90
    frame[3] = 0xEB
    frame[4] = reg & 0xFF
    frame[5] = length & 0xFF
    frame[6:10] = vals4[:4]
    frame[19] = crc_simple(frame, 19)
    return frame


async def write_register(client: BleakClient, write_target, reg: int, vals4: bytearray, length: int, await_s: float = 0.0):
    frame = build_write_frame(reg, vals4, length)
    await client.write_gatt_char(write_target, frame, response=False)
    if await_s > 0:
        await asyncio.sleep(await_s)
    return {"reg": reg, "len": length, "data": vals4[:length].hex(), "frame": frame.hex()}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--adapter", default=None, help="BlueZ adapter name, e.g. hci1")
    ap.add_argument("--dry-run", action="store_true")

    ap.add_argument("--set-ovp", type=float, default=None, help="Cell OVP in V (e.g. 3.65)")
    ap.add_argument("--set-ovpr", type=float, default=None, help="Cell OVPR in V (e.g. 3.60)")

    ap.add_argument("--soc-reset", action="store_true", help="SOC reset trick (lowers ovp/ovpr briefly)")
    ap.add_argument("--max-cell-v", type=float, default=None, help="Required for --soc-reset if no live value is known")

    ap.add_argument("--ctrl-on", action="store_true", help="EXPERIMENTAL: write 01.. to control regs 1D/1E/1F/40")
    ap.add_argument("--ctrl-off", action="store_true", help="EXPERIMENTAL: write 00.. to control regs 1D/1E/1F/40")
    ap.add_argument("--force", action="store_true", help="Required for experimental control writes")

    args = ap.parse_args()

    ops = []
    if args.set_ovp is not None:
        ops.append(("set_ovp", args.set_ovp))
    if args.set_ovpr is not None:
        ops.append(("set_ovpr", args.set_ovpr))
    if args.soc_reset:
        ops.append(("soc_reset", None))
    if args.ctrl_on or args.ctrl_off:
        if not args.force:
            raise SystemExit("Refusing experimental control writes without --force")
        ops.append(("ctrl", "on" if args.ctrl_on else "off"))

    if not ops:
        raise SystemExit("No operation specified")

    planned = {"address": args.address, "adapter": args.adapter, "ops": ops, "dry_run": args.dry_run}
    if args.dry_run:
        print(json.dumps({"planned": planned}, ensure_ascii=False))
        return

    results = {"address": args.address, "adapter": args.adapter, "ts": time.time(), "ops": []}

    async with BleakClient(args.address, timeout=args.timeout, adapter=args.adapter) as client:
        # pick write characteristic
        try:
            # read properties by trying notify start is overkill; just try write UUID.
            write_target = CHAR_UUID
        except Exception:
            write_target = CHAR_HANDLE_FAILOVER

        # Determine which target works by attempting a harmless 0-length write? not possible.
        # We'll try UUID first, fallback to handle on BleakError.
        async def wr(reg, vals4, length, await_s=0.0):
            try:
                return await write_register(client, CHAR_UUID, reg, vals4, length, await_s=await_s)
            except Exception:
                return await write_register(client, CHAR_HANDLE_FAILOVER, reg, vals4, length, await_s=await_s)

        for op, val in ops:
            if op == "set_ovp":
                results["ops"].append({"op": "set_ovp", "value_v": val, "write": await wr(JK_REGISTER_OVP, jk_float_to_hex_little(val), 4, await_s=0.5)})
            elif op == "set_ovpr":
                results["ops"].append({"op": "set_ovpr", "value_v": val, "write": await wr(JK_REGISTER_OVPR, jk_float_to_hex_little(val), 4, await_s=0.5)})
            elif op == "soc_reset":
                if args.max_cell_v is None:
                    raise SystemExit("--soc-reset requires --max-cell-v (for now)")
                ovp_trigger = round(args.max_cell_v - 0.05, 3)
                ovpr_trigger = round(args.max_cell_v - 0.10, 3)
                w1 = await wr(JK_REGISTER_OVPR, jk_float_to_hex_little(ovpr_trigger), 4, await_s=0.5)
                w2 = await wr(JK_REGISTER_OVP, jk_float_to_hex_little(ovp_trigger), 4, await_s=0.5)
                await asyncio.sleep(5)
                results["ops"].append({"op": "soc_reset", "max_cell_v": args.max_cell_v, "ovpr_trigger": ovpr_trigger, "ovp_trigger": ovp_trigger, "writes": [w1, w2]})
            elif op == "ctrl":
                on = (val == "on")
                payload = b"\x01\x00\x00\x00" if on else b"\x00\x00\x00\x00"
                regs = [0x1D, 0x1E, 0x1F, 0x40]
                writes = []
                for r in regs:
                    writes.append(await wr(r, bytearray(payload), 4, await_s=0.2))
                results["ops"].append({"op": "ctrl_buttons_experimental", "state": val, "regs": regs, "writes": writes})

    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

