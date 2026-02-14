#!/usr/bin/env python3
"""
JK-BMS BLE read (device + cell info, and settings if the BMS provides them).

Design goals:
- Works on Raspberry Pi/BlueZ using bleak
- Output is JSON, easy to consume from Node-RED/Python/Java/etc.
- Decoder is based on Louisvdw/dbus-serialbattery (jkbms_brn.py)

NOTE: This is read-only. For writes use jk_ble_write.py.
"""

import argparse
import asyncio
import json
import time
from struct import unpack_from, calcsize

from bleak import BleakClient, exc

CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
CHAR_HANDLE_FAILOVER = 4
MODEL_NBR_UUID = "00002a24-0000-1000-8000-00805f9b34fb"

COMMAND_CELL_INFO = 0x96
COMMAND_DEVICE_INFO = 0x97

PROTOCOL_VERSION_JK02 = 0x02
protocol_version = PROTOCOL_VERSION_JK02

MIN_RESPONSE_SIZE = 300
MAX_RESPONSE_SIZE = 320


TRANSLATE_DEVICE_INFO = [
    [["device_info", "hw_rev"], 22, "8s"],
    [["device_info", "sw_rev"], 30, "8s"],
    [["device_info", "uptime"], 38, "<L"],
    [["device_info", "vendor_id"], 6, "16s"],
    [["device_info", "manufacturing_date"], 78, "8s"],
    [["device_info", "serial_number"], 86, "10s"],
    [["device_info", "production"], 102, "8s"],
]

TRANSLATE_SETTINGS = [
    [["settings", "cell_uvp"], 10, "<L", 0.001],
    [["settings", "cell_uvpr"], 14, "<L", 0.001],
    [["settings", "cell_ovp"], 18, "<L", 0.001],
    [["settings", "cell_ovpr"], 22, "<L", 0.001],
    [["settings", "balance_trigger_voltage"], 26, "<L", 0.001],
    [["settings", "power_off_voltage"], 46, "<L", 0.001],
    [["settings", "max_charge_current"], 50, "<L", 0.001],
    [["settings", "max_discharge_current"], 62, "<L", 0.001],
    [["settings", "max_balance_current"], 50, "<L", 0.001],
    [["settings", "cell_count"], 114, "<L"],
    [["settings", "charging_switch"], 118, "4?"],
    [["settings", "discharging_switch"], 122, "4?"],
    [["settings", "balancing_switch"], 126, "4?"],
]

TRANSLATE_CELL_INFO_24S = [
    [["cell_info", "voltages", 32], 6, "<H", 0.001],
    [["cell_info", "average_cell_voltage"], 58, "<H", 0.001],
    [["cell_info", "delta_cell_voltage"], 60, "<H", 0.001],
    [["cell_info", "max_voltage_cell"], 62, "<B"],
    [["cell_info", "min_voltage_cell"], 63, "<B"],
    [["cell_info", "resistances", 32], 64, "<H", 0.001],
    [["cell_info", "total_voltage"], 118, "<H", 0.001],
    [["cell_info", "current"], 126, "<l", 0.001],
    [["cell_info", "temperature_sensor_1"], 130, "<H", 0.1],
    [["cell_info", "temperature_sensor_2"], 132, "<H", 0.1],
    [["cell_info", "temperature_mos"], 134, "<H", 0.1],
    [["cell_info", "balancing_current"], 138, "<H", 0.001],
    [["cell_info", "balancing_action"], 140, "<B", 0.001],
    [["cell_info", "battery_soc"], 141, "B"],
    [["cell_info", "capacity_remain"], 142, "<L", 0.001],
    [["cell_info", "capacity_nominal"], 146, "<L", 0.001],
    [["cell_info", "cycle_count"], 150, "<L"],
    [["cell_info", "cycle_capacity"], 154, "<L", 0.001],
    [["cell_info", "charging_switch_enabled"], 166, "1?"],
    [["cell_info", "discharging_switch_enabled"], 167, "1?"],
    [["cell_info", "balancing_active"], 191, "1?"],
]

TRANSLATE_CELL_INFO_32S = [
    [["cell_info", "voltages", 32], 6, "<H", 0.001],
    [["cell_info", "average_cell_voltage"], 58, "<H", 0.001],
    [["cell_info", "delta_cell_voltage"], 60, "<H", 0.001],
    [["cell_info", "max_voltage_cell"], 62, "<B"],
    [["cell_info", "min_voltage_cell"], 63, "<B"],
    [["cell_info", "resistances", 32], 64, "<H", 0.001],
    [["cell_info", "total_voltage"], 118, "<H", 0.001],
    [["cell_info", "current"], 126, "<l", 0.001],
    [["cell_info", "temperature_sensor_1"], 130, "<H", 0.1],
    [["cell_info", "temperature_sensor_2"], 132, "<H", 0.1],
    [["cell_info", "temperature_mos"], 112, "<H", 0.1],
    [["cell_info", "balancing_current"], 138, "<H", 0.001],
    [["cell_info", "balancing_action"], 140, "<B", 0.001],
    [["cell_info", "battery_soc"], 141, "B"],
    [["cell_info", "capacity_remain"], 142, "<L", 0.001],
    [["cell_info", "capacity_nominal"], 146, "<L", 0.001],
    [["cell_info", "cycle_count"], 150, "<L"],
    [["cell_info", "cycle_capacity"], 154, "<L", 0.001],
    [["cell_info", "charging_switch_enabled"], 166, "1?"],
    [["cell_info", "discharging_switch_enabled"], 167, "1?"],
    [["cell_info", "balancing_active"], 191, "1?"],
]


def crc_simple(arr: bytearray, length: int) -> int:
    c = 0
    for a in arr[:length]:
        c += a
    return c.to_bytes(2, "little")[0]


def build_request_frame(cmd: int) -> bytearray:
    frame = bytearray(20)
    frame[0] = 0xAA
    frame[1] = 0x55
    frame[2] = 0x90
    frame[3] = 0xEB
    frame[4] = cmd
    frame[5] = 0x00
    frame[19] = crc_simple(frame, 19)
    return frame


class JKDecoder:
    def __init__(self):
        self.frame_buffer = bytearray()
        self.bms_status = {"last_update": None}
        self.waiting_for = ""
        self.last_cell_info = 0
        self.bms_max_cell_count = None
        self.translate_cell_info = []

    def get_bms_max_cell_count(self):
        fb = self.frame_buffer
        if len(fb) < 292:
            self.bms_max_cell_count = 24
            self.translate_cell_info = TRANSLATE_CELL_INFO_24S
            return
        if fb[287] > 0:
            self.bms_max_cell_count = 32
            self.translate_cell_info = TRANSLATE_CELL_INFO_32S
        else:
            self.bms_max_cell_count = 24
            self.translate_cell_info = TRANSLATE_CELL_INFO_24S

    def translate(self, fb, translation, o, f32s=False, i=0):
        if i == len(translation[0]) - 1:
            keys = range(0, translation[0][i]) if isinstance(translation[0][i], int) else [translation[0][i]]
            offset = 0
            if f32s:
                if translation[1] >= 112:
                    offset = 32
                elif translation[1] >= 54:
                    offset = 16
            step = 0
            for j in keys:
                if isinstance(translation[2], int):
                    val = bytearray(fb[translation[1] + step + offset : translation[1] + step + translation[2] + offset])
                    step += translation[2]
                else:
                    val = unpack_from(translation[2], bytearray(fb), translation[1] + step + offset)[0]
                    step += calcsize(translation[2])

                if isinstance(val, bytes):
                    try:
                        val = val.decode("utf-8").rstrip(" \t\n\r\0")
                    except UnicodeDecodeError:
                        val = ""
                elif isinstance(val, int) and len(translation) == 4:
                    val = val * translation[3]
                o[j] = val
        else:
            k = translation[0][i]
            if k not in o:
                if len(translation[0]) == i + 2 and isinstance(translation[0][i + 1], int):
                    o[k] = [None] * translation[0][i + 1]
                else:
                    o[k] = {}
            self.translate(fb, translation, o[k], f32s=f32s, i=i + 1)

    def decode_warnings(self, fb):
        val = unpack_from("<H", bytearray(fb), 136)[0]
        self.bms_status.setdefault("cell_info", {})
        self.bms_status["cell_info"]["error_bitmask_16"] = hex(val)
        self.bms_status["cell_info"]["error_bitmask_2"] = format(val, "016b")
        w = self.bms_status.setdefault("warnings", {})
        w["resistance_too_high"] = bool(val & (1 << 0))
        w["cell_count_wrong"] = bool(val & (1 << 2))
        w["charge_overtemp"] = bool(val & (1 << 8))
        w["charge_undertemp"] = bool(val & (1 << 9))
        w["discharge_overtemp"] = bool(val & (1 << 15))
        w["cell_overvoltage"] = bool(val & (1 << 4))
        w["cell_undervoltage"] = bool(val & (1 << 11))
        w["charge_overcurrent"] = bool(val & (1 << 6))
        w["discharge_overcurrent"] = bool(val & (1 << 13))

    def decode_device_info(self):
        fb = self.frame_buffer
        for t in TRANSLATE_DEVICE_INFO:
            self.translate(fb, t, self.bms_status)

    def decode_settings(self):
        fb = self.frame_buffer
        for t in TRANSLATE_SETTINGS:
            self.translate(fb, t, self.bms_status)

        # adapt translation for real cell_count if present
        try:
            ccount = int(self.bms_status["settings"]["cell_count"])
            for i, t in enumerate(self.translate_cell_info):
                if len(t[0]) >= 3 and t[0][-2] in ("voltages", "resistances"):
                    self.translate_cell_info[i][0][-1] = ccount
        except Exception:
            pass

    def decode_cell_info(self):
        fb = self.frame_buffer
        has32s = self.bms_max_cell_count == 32
        for t in self.translate_cell_info:
            self.translate(fb, t, self.bms_status, f32s=has32s)
        self.decode_warnings(fb)

        # Derived convenience: power
        try:
            ci = self.bms_status.get("cell_info", {})
            if "current" in ci and "total_voltage" in ci:
                ci["power"] = ci["current"] * ci["total_voltage"]
        except Exception:
            pass

        # If total_voltage looks wrong, derive from voltages
        try:
            ci = self.bms_status.get("cell_info", {})
            vols = ci.get("voltages") or []
            vols = [v for v in vols if v is not None and v > 0]
            if vols:
                s = float(sum(vols))
                if float(ci.get("total_voltage") or 0) < 1.0:
                    ci["total_voltage"] = round(s, 3)
        except Exception:
            pass

    def assemble_and_maybe_decode(self, data: bytearray):
        if len(self.frame_buffer) > MAX_RESPONSE_SIZE:
            self.frame_buffer = bytearray()

        if len(data) >= 4 and data[0:4] == b"\x55\xAA\xEB\x90":
            self.frame_buffer = bytearray()

        self.frame_buffer.extend(data)

        if len(self.frame_buffer) >= MIN_RESPONSE_SIZE:
            calc = crc_simple(self.frame_buffer, MIN_RESPONSE_SIZE - 1)
            rx = self.frame_buffer[MIN_RESPONSE_SIZE - 1]
            if calc != rx:
                return None

            info_type = self.frame_buffer[4]
            self.get_bms_max_cell_count()

            if info_type == 0x01:
                self.decode_settings()
                self.bms_status["last_update"] = time.time()
                self.waiting_for = ""
                return "settings"
            if info_type == 0x02:
                self.decode_cell_info()
                self.bms_status["last_update"] = time.time()
                if self.waiting_for == "cell_info":
                    self.waiting_for = ""
                return "cell_info"
            if info_type == 0x03:
                self.decode_device_info()
                self.bms_status["last_update"] = time.time()
                if self.waiting_for == "device_info":
                    self.waiting_for = ""
                return "device_info"

        return None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--adapter", default=None, help="BlueZ adapter name, e.g. hci1")
    args = ap.parse_args()

    dec = JKDecoder()

    async with BleakClient(args.address, timeout=args.timeout, adapter=args.adapter) as client:
        model_nbr = None
        try:
            model_nbr = (await client.read_gatt_char(MODEL_NBR_UUID)).decode("utf-8", errors="ignore").strip()
        except Exception:
            model_nbr = None

        got = {"device_info": False, "cell_info": False, "settings": False}

        def ncb(sender: int, data: bytearray):
            kind = dec.assemble_and_maybe_decode(bytearray(data))
            if kind in got:
                got[kind] = True

        # notify setup (UUID -> handle failover)
        try:
            await client.start_notify(CHAR_UUID, ncb)
            write_target = CHAR_UUID
        except exc.BleakError:
            await client.start_notify(CHAR_HANDLE_FAILOVER, ncb)
            write_target = CHAR_HANDLE_FAILOVER

        async def send_device():
            await client.write_gatt_char(write_target, build_request_frame(COMMAND_DEVICE_INFO), response=False)

        async def send_cell():
            await client.write_gatt_char(write_target, build_request_frame(COMMAND_CELL_INFO), response=False)

        # initial burst
        await send_device()
        await asyncio.sleep(0.2)
        await send_cell()

        # Some JK firmwares are flaky with one-off requests; retry until timeout.
        t_end = time.time() + args.timeout
        t_next_dev = time.time() + 2.0
        t_next_cell = time.time() + 2.0
        while time.time() < t_end and not (got["device_info"] and got["cell_info"]):
            now = time.time()
            if not got["device_info"] and now >= t_next_dev:
                await send_device()
                t_next_dev = now + 2.0
            if not got["cell_info"] and now >= t_next_cell:
                await send_cell()
                t_next_cell = now + 2.0
            await asyncio.sleep(0.05)

        try:
            await client.stop_notify(write_target)
        except Exception:
            pass

        # Derivations for consumers (Node-RED/UI):
        try:
            ci = dec.bms_status.get("cell_info", {})
            v = ci.get("voltages") or []
            r = ci.get("resistances") or []
            # infer cell_count if settings missing
            inferred = 0
            for x in v:
                if x and x > 0:
                    inferred += 1
            if inferred:
                ci["cell_count_inferred"] = inferred
                ci["voltages"] = v[:inferred]
                if r:
                    ci["resistances"] = r[:inferred]
        except Exception:
            pass

        out = {
            "address": args.address,
            "adapter": args.adapter,
            "connected": client.is_connected,
            "model_nbr": model_nbr,
            "got": got,
            "status": dec.bms_status,
        }
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
