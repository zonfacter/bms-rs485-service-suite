#!/usr/bin/env python3
"""
JK-BMS BLE probe (read-only).

This is based on the logic used in Louisvdw/dbus-serialbattery:
- notify/write on UUID 0000ffe1-0000-1000-8000-00805f9b34fb (fallback handle 4)
- request device info (0x97) and cell info (0x96) by writing a 20-byte command frame
- assemble 300-byte responses starting with 55 AA EB 90 and validate simple checksum
"""

import argparse
import asyncio
import time
from struct import unpack_from
from bleak import BleakClient, exc

CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
CHAR_HANDLE_FAILOVER = 4
MODEL_NBR_UUID = "00002a24-0000-1000-8000-00805f9b34fb"

CMD_CELL_INFO = 0x96
CMD_DEVICE_INFO = 0x97

MIN_RESPONSE_SIZE = 300
MAX_RESPONSE_SIZE = 320


def crc_simple(arr: bytearray, length: int) -> int:
    c = 0
    for a in arr[:length]:
        c += a
    # little endian, low byte
    return c.to_bytes(2, "little")[0]


def build_request_frame(cmd: int) -> bytearray:
    frame = bytearray(20)
    frame[0] = 0xAA
    frame[1] = 0x55
    frame[2] = 0x90
    frame[3] = 0xEB
    frame[4] = cmd
    frame[5] = 0x00  # length
    # 6..18 already 0
    frame[19] = crc_simple(frame, 19)
    return frame


class Assembler:
    def __init__(self):
        self.buf = bytearray()
        self.last_good = None  # (info_type, buf)

    def feed(self, data: bytearray):
        if len(self.buf) > MAX_RESPONSE_SIZE:
            self.buf = bytearray()

        # Start of new frame marker (per dbus-serialbattery)
        if len(data) >= 4 and data[0:4] == b"\x55\xAA\xEB\x90":
            self.buf = bytearray()

        self.buf.extend(data)

        if len(self.buf) >= MIN_RESPONSE_SIZE:
            calc = crc_simple(self.buf, MIN_RESPONSE_SIZE - 1)
            rx = self.buf[MIN_RESPONSE_SIZE - 1]
            if calc == rx:
                info_type = self.buf[4]
                self.last_good = (info_type, bytes(self.buf[:MIN_RESPONSE_SIZE]))
                self.buf = bytearray()
                return self.last_good
        return None


def decode_device_info(frame300: bytes) -> dict:
    # vendor_id at offset 6, 16 bytes (as in dbus-serialbattery translate table)
    vendor = frame300[6 : 6 + 16].decode("utf-8", errors="ignore").rstrip(" \t\r\n\0")
    hw = frame300[22 : 22 + 8].decode("utf-8", errors="ignore").rstrip(" \t\r\n\0")
    sw = frame300[30 : 30 + 8].decode("utf-8", errors="ignore").rstrip(" \t\r\n\0")
    serial = frame300[86 : 86 + 10].decode("utf-8", errors="ignore").rstrip(" \t\r\n\0")
    mfg_date = frame300[78 : 78 + 8].decode("utf-8", errors="ignore").rstrip(" \t\r\n\0")
    return {
        "vendor_id": vendor,
        "hw_rev": hw,
        "sw_rev": sw,
        "serial_number": serial,
        "manufacturing_date": mfg_date,
    }


def decode_cell_info(frame300: bytes, max_cells: int = 32) -> dict:
    # The dbus-serialbattery tables are more complete; here we just decode core values.
    total_v = unpack_from("<H", frame300, 118)[0] * 0.001
    current_a = unpack_from("<l", frame300, 126)[0] * 0.001
    soc = frame300[141]
    voltages = []
    # cell voltages from offset 6, <H, 0.001, count unknown here; decode up to max_cells
    for i in range(max_cells):
        mv = unpack_from("<H", frame300, 6 + i * 2)[0] * 0.001
        voltages.append(mv)
    # trim trailing zeros (common when max_cells > actual)
    while voltages and voltages[-1] == 0.0:
        voltages.pop()
    return {"total_voltage_v": total_v, "current_a": current_a, "soc_pct": soc, "cell_voltages_v": voltages}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("address", help="BLE address, e.g. C8:47:80:37:02:E8")
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--tries", type=int, default=2)
    args = ap.parse_args()

    addr = args.address
    for attempt in range(1, args.tries + 1):
        asm = Assembler()
        got = {"device_info": None, "cell_info": None, "model_nbr": None}
        t_end = time.time() + args.timeout

        try:
            async with BleakClient(addr, timeout=args.timeout) as client:
                # optional model number
                try:
                    got["model_nbr"] = (await client.read_gatt_char(MODEL_NBR_UUID)).decode("utf-8", errors="ignore").strip()
                except Exception:
                    got["model_nbr"] = None

                async def ncb(sender: int, data: bytearray):
                    res = asm.feed(data)
                    if res is None:
                        return
                    info_type, fr = res
                    if info_type == 0x03 and got["device_info"] is None:
                        got["device_info"] = decode_device_info(fr)
                    elif info_type == 0x02 and got["cell_info"] is None:
                        got["cell_info"] = decode_cell_info(fr)

                # start notify (UUID -> fallback handle)
                try:
                    await client.start_notify(CHAR_UUID, ncb)
                    write_target = CHAR_UUID
                except exc.BleakError:
                    await client.start_notify(CHAR_HANDLE_FAILOVER, ncb)
                    write_target = CHAR_HANDLE_FAILOVER

                # send requests
                await client.write_gatt_char(write_target, build_request_frame(CMD_DEVICE_INFO), response=False)
                await asyncio.sleep(0.2)
                await client.write_gatt_char(write_target, build_request_frame(CMD_CELL_INFO), response=False)

                while time.time() < t_end and (got["device_info"] is None or got["cell_info"] is None):
                    await asyncio.sleep(0.05)

                try:
                    await client.stop_notify(write_target)
                except Exception:
                    pass

                print(f"address={addr} connected={client.is_connected} attempt={attempt}")
                print(f"model_nbr={got['model_nbr']}")
                print(f"device_info={got['device_info']}")
                print(f"cell_info={got['cell_info']}")
                return

        except Exception as e:
            print(f"address={addr} attempt={attempt} ERROR: {repr(e)}")

        await asyncio.sleep(0.5)

    raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())

