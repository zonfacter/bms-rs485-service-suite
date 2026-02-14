#!/usr/bin/env python3
"""
JK-BMS BLE write operations.

Implemented:
- Set multiple settings via holding registers (based on syssi/esphome-jk-bms register map)
- SOC reset trick (temporarily lower OVP/OVPR)
- Switch toggles via holding registers (charging/discharging/balancer, etc.)

WARNING:
- Writes can permanently change BMS behavior.
- Start with --dry-run and verify values.
"""

import argparse
import asyncio
import json
import time

from bleak import BleakClient

CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
CHAR_HANDLE_FAILOVER = 4

PROTO_JK02_24S = "jk02_24s"
PROTO_JK02_32S = "jk02_32s"

# Register maps derived from syssi/esphome-jk-bms:
# - components/jk_bms_ble/number/__init__.py (NUMBERS)
# - components/jk_bms_ble/switch/__init__.py (SWITCHES)

# Numbers: key -> (jk02_24s_reg, jk02_32s_reg, factor, length)
NUM = {
    "cell_uvp_v": (0x02, 0x02, 1000.0, 4),
    "cell_uvpr_v": (0x03, 0x03, 1000.0, 4),
    "cell_ovp_v": (0x04, 0x04, 1000.0, 4),
    "cell_ovpr_v": (0x05, 0x05, 1000.0, 4),
    "balance_trigger_v": (0x06, 0x06, 1000.0, 4),
    "power_off_v": (0x0B, 0x0B, 1000.0, 4),
    "max_charge_a": (0x0C, 0x0C, 1000.0, 4),
    "max_discharge_a": (0x0F, 0x0F, 1000.0, 4),
    "max_balance_a": (0x13, 0x13, 1000.0, 4),
    "balance_start_v": (0x26, 0x22, 1000.0, 4),
    "cell_req_charge_v": (0x09, 0x09, 1000.0, 4),
    "cell_req_float_v": (0x0A, 0x0A, 1000.0, 4),
    "cell_soc100_v": (0x07, 0x07, 1000.0, 4),
    "cell_soc0_v": (0x08, 0x08, 1000.0, 4),
}

# Switches: key -> (jk02_24s_reg, jk02_32s_reg, length)
SW = {
    "charging": (0x1D, 0x1D, 4),
    "discharging": (0x1E, 0x1E, 4),
    "balancer": (0x1F, 0x1F, 4),
    # 32S extras (only valid on jk02_32s; keep for completeness)
    "emergency": (None, 0x6B, 4),
    "heating": (None, 0x27, 4),
    "disable_temperature_sensors": (None, 0x28, 4),
    "display_always_on": (None, 0x2B, 4),
    "smart_sleep": (None, 0x2D, 4),
    "disable_pcl_module": (None, 0x2E, 4),
    "timed_stored_data": (None, 0x2F, 4),
    "charging_float_mode": (None, 0x30, 4),
}


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

def u32_to_le_bytes(v: int) -> bytearray:
    v = int(v) & 0xFFFFFFFF
    return bytearray([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF])


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


def pick_reg(proto: str, reg24, reg32):
    if proto == PROTO_JK02_32S:
        return reg32
    return reg24


def build_number_write(proto: str, key: str, value: float) -> tuple[int, bytearray, int, dict]:
    if key not in NUM:
        raise KeyError(key)
    reg24, reg32, factor, length = NUM[key]
    reg = pick_reg(proto, reg24, reg32)
    if reg is None:
        raise ValueError(f"{key} not supported for proto={proto}")
    raw = int(round(float(value) * factor))
    vals4 = u32_to_le_bytes(raw)
    meta = {"key": key, "value": float(value), "factor": factor, "raw_u32": raw}
    return reg, vals4, length, meta


def build_switch_write(proto: str, key: str, state: bool) -> tuple[int, bytearray, int, dict]:
    if key not in SW:
        raise KeyError(key)
    reg24, reg32, length = SW[key]
    reg = pick_reg(proto, reg24, reg32)
    if reg is None:
        raise ValueError(f"{key} not supported for proto={proto}")
    vals4 = u32_to_le_bytes(1 if state else 0)
    meta = {"key": key, "state": bool(state)}
    return reg, vals4, length, meta


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--adapter", default=None, help="BlueZ adapter name, e.g. hci1")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--proto", default="auto", help="auto|jk02_24s|jk02_32s")

    # Numbers (cell-based voltages/currents)
    ap.add_argument("--set-uvp", type=float, default=None, help="Cell UVP in V")
    ap.add_argument("--set-uvpr", type=float, default=None, help="Cell UVPR in V")
    ap.add_argument("--set-ovp", type=float, default=None, help="Cell OVP in V")
    ap.add_argument("--set-ovpr", type=float, default=None, help="Cell OVPR in V")
    ap.add_argument("--set-balance-trigger", type=float, default=None, help="Balance trigger voltage in V")
    ap.add_argument("--set-balance-start", type=float, default=None, help="Start balance voltage in V")
    ap.add_argument("--set-power-off", type=float, default=None, help="Power off voltage in V")
    ap.add_argument("--set-max-charge", type=float, default=None, help="Max charge current in A")
    ap.add_argument("--set-max-discharge", type=float, default=None, help="Max discharge current in A")
    ap.add_argument("--set-max-balance", type=float, default=None, help="Max balance current in A")
    ap.add_argument("--set-req-charge-v", type=float, default=None, help="Requested charge voltage (cell) in V")
    ap.add_argument("--set-req-float-v", type=float, default=None, help="Requested float voltage (cell) in V")
    ap.add_argument("--set-soc100-v", type=float, default=None, help="SOC 100%% voltage (cell) in V")
    ap.add_argument("--set-soc0-v", type=float, default=None, help="SOC 0%% voltage (cell) in V")

    ap.add_argument("--soc-reset", action="store_true", help="SOC reset trick (lowers ovp/ovpr briefly)")
    ap.add_argument("--max-cell-v", type=float, default=None, help="Required for --soc-reset if no live value is known")

    # Switches
    ap.add_argument("--charging", choices=["on", "off"], default=None)
    ap.add_argument("--discharging", choices=["on", "off"], default=None)
    ap.add_argument("--balancer", choices=["on", "off"], default=None)
    ap.add_argument("--force", action="store_true", help="Required for switch toggles and any write")

    args = ap.parse_args()

    ops = []

    def add_num(flag_name, key):
        val = getattr(args, flag_name)
        if val is not None:
            ops.append(("num", key, float(val)))

    add_num("set_uvp", "cell_uvp_v")
    add_num("set_uvpr", "cell_uvpr_v")
    add_num("set_ovp", "cell_ovp_v")
    add_num("set_ovpr", "cell_ovpr_v")
    add_num("set_balance_trigger", "balance_trigger_v")
    add_num("set_balance_start", "balance_start_v")
    add_num("set_power_off", "power_off_v")
    add_num("set_max_charge", "max_charge_a")
    add_num("set_max_discharge", "max_discharge_a")
    add_num("set_max_balance", "max_balance_a")
    add_num("set_req_charge_v", "cell_req_charge_v")
    add_num("set_req_float_v", "cell_req_float_v")
    add_num("set_soc100_v", "cell_soc100_v")
    add_num("set_soc0_v", "cell_soc0_v")

    if args.soc_reset:
        ops.append(("soc_reset", None, None))

    def add_sw(flag_name, key):
        val = getattr(args, flag_name)
        if val is None:
            return
        ops.append(("sw", key, True if val == "on" else False))

    add_sw("charging", "charging")
    add_sw("discharging", "discharging")
    add_sw("balancer", "balancer")

    if not ops:
        raise SystemExit("No operation specified")

    if not args.force:
        raise SystemExit("Refusing to write without --force (safety). Use --dry-run to inspect planned ops.")

    # Protocol selection (auto: default to jk02_24s unless user forces 32s)
    proto = args.proto.strip().lower()
    if proto == "auto":
        proto = PROTO_JK02_24S
    elif proto not in (PROTO_JK02_24S, PROTO_JK02_32S):
        raise SystemExit("Invalid --proto. Use auto|jk02_24s|jk02_32s")

    planned = {"address": args.address, "adapter": args.adapter, "ops": ops, "dry_run": args.dry_run}
    if args.dry_run:
        planned["proto"] = proto
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

        results["proto"] = proto

        for op, key, val in ops:
            if op == "num":
                reg, vals4, length, meta = build_number_write(proto, key, val)
                results["ops"].append({"op": "set_number", **meta, "reg": reg, "write": await wr(reg, vals4, length, await_s=0.4)})
            elif op == "sw":
                reg, vals4, length, meta = build_switch_write(proto, key, val)
                results["ops"].append({"op": "set_switch", **meta, "reg": reg, "write": await wr(reg, vals4, length, await_s=0.4)})
            elif op == "soc_reset":
                if args.max_cell_v is None:
                    raise SystemExit("--soc-reset requires --max-cell-v (for now)")
                ovp_trigger = round(args.max_cell_v - 0.05, 3)
                ovpr_trigger = round(args.max_cell_v - 0.10, 3)
                r_ovpr, v_ovpr, l_ovpr, _ = build_number_write(proto, "cell_ovpr_v", ovpr_trigger)
                r_ovp, v_ovp, l_ovp, _ = build_number_write(proto, "cell_ovp_v", ovp_trigger)
                w1 = await wr(r_ovpr, v_ovpr, l_ovpr, await_s=0.5)
                w2 = await wr(r_ovp, v_ovp, l_ovp, await_s=0.5)
                await asyncio.sleep(5)
                results["ops"].append({"op": "soc_reset", "max_cell_v": args.max_cell_v, "ovpr_trigger": ovpr_trigger, "ovp_trigger": ovp_trigger, "writes": [w1, w2]})

    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
