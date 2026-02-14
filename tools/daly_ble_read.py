#!/usr/bin/env python3
"""
Daly Smart BMS BLE reader (A5 protocol, 13-byte frames) over BLE UART-style service.

Tested against devices advertising as "Akku-2"/"Akku-3" with:
  Service: 0000fff0-0000-1000-8000-00805f9b34fb
    Notify: 0000fff1-0000-1000-8000-00805f9b34fb
    Write : 0000fff2-0000-1000-8000-00805f9b34fb

Output: always JSON (for Node-RED/MQTT).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from bleak import BleakClient, BleakScanner, exc
from bleak.backends.device import BLEDevice
from bleak.backends.bluezdbus.manager import get_global_bluez_manager

NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"


def _now() -> float:
    return time.time()


def _u16be(b: bytes, i: int) -> int:
    return ((b[i] & 0xFF) << 8) | (b[i + 1] & 0xFF)


def _checksum(frame_12: bytes) -> int:
    return sum(frame_12) & 0xFF


def build_request(cmd: int) -> bytes:
    fr = bytearray([0xA5, 0x40, cmd & 0xFF, 0x08] + [0x00] * 8)
    fr.append(_checksum(fr))
    return bytes(fr)


def verify_frame(fr: bytes) -> bool:
    if len(fr) != 13:
        return False
    if fr[0] != 0xA5:
        return False
    if fr[3] != 0x08:
        return False
    return _checksum(fr[:12]) == (fr[12] & 0xFF)


def split_frames(buf: bytearray) -> List[bytes]:
    """
    Extract valid 13-byte frames (start byte 0xA5) from an arbitrary byte stream.
    Keeps leftovers in the buffer.
    """
    out: List[bytes] = []
    i = 0
    # scan forward, extracting contiguous frames
    while True:
        # find start
        while i < len(buf) and buf[i] != 0xA5:
            i += 1
        if i >= len(buf):
            break
        if len(buf) - i < 13:
            break
        cand = bytes(buf[i : i + 13])
        if verify_frame(cand):
            out.append(cand)
            i += 13
            continue
        # false positive start byte; skip it
        i += 1
    # drop consumed bytes
    if i > 0:
        del buf[:i]
    # cap runaway buffer
    if len(buf) > 4096:
        del buf[:-1024]
    return out


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


@dataclass
class DalyState:
    frames: Dict[int, bytes]
    cell_frames: Dict[int, bytes]
    temp_frames: Dict[int, bytes]
    last_rx: float
    cell_count: Optional[int] = None
    temp_count: Optional[int] = None
    cells_mv: Optional[List[int]] = None
    temps_c: Optional[List[float]] = None


def decode_90(payload: bytes) -> Dict[str, Any]:
    # per DALY CAN protocol: V_total (0.1V), V_gather (0.1V), current (0.1A, 30000 offset), SOC (0.1%)
    v_total = _u16be(payload, 0) / 10.0
    v_gather = _u16be(payload, 2) / 10.0
    raw_i = _u16be(payload, 4)
    current_a = (raw_i - 30000) / 10.0
    soc = _u16be(payload, 6) / 10.0
    return {"voltage_total_v": round(v_total, 3), "voltage_gather_v": round(v_gather, 3), "current_a": round(current_a, 3), "soc_pct": round(soc, 1)}


def decode_91(payload: bytes) -> Dict[str, Any]:
    # max cell mV, max cell no, min cell mV, min cell no
    max_mv = _u16be(payload, 0)
    max_no = payload[2]
    min_mv = _u16be(payload, 3)
    min_no = payload[5]
    return {
        "cell_max_v": round(max_mv / 1000.0, 3),
        "cell_max_no": int(max_no),
        "cell_min_v": round(min_mv / 1000.0, 3),
        "cell_min_no": int(min_no),
        "cell_delta_v": round((max_mv - min_mv) / 1000.0, 3),
    }


def decode_92(payload: bytes) -> Dict[str, Any]:
    # temps are (value - 40) in C
    tmax = int(payload[0]) - 40
    tmax_no = int(payload[1])
    tmin = int(payload[2]) - 40
    tmin_no = int(payload[3])
    return {"temp_max_c": tmax, "temp_max_no": tmax_no, "temp_min_c": tmin, "temp_min_no": tmin_no}


def decode_93(payload: bytes) -> Dict[str, Any]:
    # state: 0 idle, 1 charge, 2 discharge
    state = int(payload[0])
    chg_mos = bool(payload[1])
    dis_mos = bool(payload[2])
    bms_life_cycles = int(payload[3])
    remain_mah = (payload[4] << 24) | (payload[5] << 16) | (payload[6] << 8) | payload[7]
    return {
        "state": state,
        "chg_mos": chg_mos,
        "dis_mos": dis_mos,
        "bms_life_cycles": bms_life_cycles,
        "remain_capacity_mah": int(remain_mah),
    }


def decode_94(payload: bytes) -> Dict[str, Any]:
    cell_count = int(payload[0])
    temp_count = int(payload[1])
    charger_status = bool(payload[2])
    load_status = bool(payload[3])
    io_bits = int(payload[4])
    cycles = (payload[5] << 8) | payload[6]
    return {
        "cell_count": cell_count,
        "temp_count": temp_count,
        "charger_connected": charger_status,
        "load_connected": load_status,
        "io_bits": io_bits,
        "charge_cycles": int(cycles),
    }


def decode_95_cells(payload: bytes) -> Dict[str, Any]:
    # frameNo + 3x u16(mV) + 1 reserved
    frame_no = int(payload[0])
    v1 = _u16be(payload, 1)
    v2 = _u16be(payload, 3)
    v3 = _u16be(payload, 5)
    return {"frame_no": frame_no, "cells_mv": [v1, v2, v3], "reserved": int(payload[7])}


def decode_96_temps(payload: bytes) -> Dict[str, Any]:
    # frameNo + up to 7 temps (byte - 40)
    frame_no = int(payload[0])
    temps = []
    for b in payload[1:]:
        if b == 0x00:
            temps.append(None)
        else:
            temps.append(int(b) - 40)
    return {"frame_no": frame_no, "temps_c": temps}


def decode_97(payload: bytes) -> Dict[str, Any]:
    # balance state bitmap(s)
    return {"raw_hex": payload.hex()}


def decode_98(payload: bytes) -> Dict[str, Any]:
    # failure codes/alarms bitmap(s)
    return {"raw_hex": payload.hex()}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--adapter", default=None, help="BlueZ adapter name, e.g. hci1")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--scan-timeout", type=float, default=10.0)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    out: Dict[str, Any] = {
        "ts": _now(),
        "address": args.address,
        "adapter": args.adapter,
        "connected": False,
        "got": {},
        "status": {},
        "error": None,
    }

    buf = bytearray()
    st = DalyState(frames={}, cell_frames={}, temp_frames={}, last_rx=_now())

    def on_notify(_: int, data: bytearray) -> None:
        buf.extend(bytearray(data))
        frames = split_frames(buf)
        if not frames:
            return
        st.last_rx = _now()
        for fr in frames:
            cmd = fr[2] & 0xFF
            if cmd == 0x95:
                # index is first payload byte
                try:
                    fn = int(fr[4])  # payload[0]
                    st.cell_frames[fn] = fr
                except Exception:
                    st.frames[cmd] = fr
            elif cmd == 0x96:
                try:
                    fn = int(fr[4])
                    st.temp_frames[fn] = fr
                except Exception:
                    st.frames[cmd] = fr
            else:
                st.frames[cmd] = fr

    async def run_once(client: BleakClient) -> int:
        async with client:
            # notifications (retry a few times because BlueZ can be flaky)
            last_err = None
            for _ in range(3):
                try:
                    await client.start_notify(NOTIFY_UUID, on_notify)
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    await asyncio.sleep(0.6)
            if last_err is not None:
                raise last_err

            # request set (order matters: 94 gives cell_count/temp_count)
            cmds = [0x94, 0x90, 0x91, 0x92, 0x93, 0x95, 0x96, 0x97, 0x98]
            for c in cmds:
                await client.write_gatt_char(WRITE_UUID, build_request(c), response=False)
                await asyncio.sleep(0.12)

            t_end = _now() + float(args.timeout)
            # wait until we have at least 90/94 (core)
            while _now() < t_end and (0x94 not in st.frames or 0x90 not in st.frames):
                await asyncio.sleep(0.05)

            # decode core first (94)
            if 0x94 in st.frames:
                payload = st.frames[0x94][4:12]
                d94 = decode_94(payload)
                out["status"]["info_94"] = d94
                out["got"]["94"] = True
                st.cell_count = d94.get("cell_count")
                st.temp_count = d94.get("temp_count")

            if 0x90 in st.frames:
                out["status"]["pack_90"] = decode_90(st.frames[0x90][4:12])
                out["got"]["90"] = True
            if 0x91 in st.frames:
                out["status"]["cell_minmax_91"] = decode_91(st.frames[0x91][4:12])
                out["got"]["91"] = True
            if 0x92 in st.frames:
                out["status"]["temp_minmax_92"] = decode_92(st.frames[0x92][4:12])
                out["got"]["92"] = True
            if 0x93 in st.frames:
                out["status"]["mos_93"] = decode_93(st.frames[0x93][4:12])
                out["got"]["93"] = True
            if 0x97 in st.frames:
                out["status"]["balance_97"] = decode_97(st.frames[0x97][4:12])
                out["got"]["97"] = True
            if 0x98 in st.frames:
                out["status"]["fault_98"] = decode_98(st.frames[0x98][4:12])
                out["got"]["98"] = True

            # assemble cells (0x95): request again and wait for all frames
            st.cell_frames.clear()
            await client.write_gatt_char(WRITE_UUID, build_request(0x95), response=False)
            await asyncio.sleep(0.15)
            t_cells_end = _now() + 2.5
            cells_by_no: Dict[int, List[int]] = {}
            frame_base: Optional[int] = None
            while _now() < t_cells_end:
                # consume any pending frames first
                _ = split_frames(buf)
                for fn, fr in list(st.cell_frames.items()):
                    d = decode_95_cells(fr[4:12])
                    if frame_base is None:
                        frame_base = 0 if int(d["frame_no"]) == 0 else 1
                    idx = int(d["frame_no"]) - frame_base
                    if idx >= 0:
                        cells_by_no[idx] = d["cells_mv"]
                await asyncio.sleep(0.05)
                if st.cell_count and cells_by_no:
                    # enough frames collected?
                    need = (int(st.cell_count) + 2) // 3  # 3 cells per frame
                    if len(cells_by_no) >= need:
                        break

            if cells_by_no:
                # flatten in order
                flat: List[int] = []
                for idx in sorted(cells_by_no.keys()):
                    flat.extend(cells_by_no[idx])
                if st.cell_count:
                    flat = flat[: int(st.cell_count)]
                out["status"]["cells_95"] = {"cells_v": [round(mv / 1000.0, 3) for mv in flat], "cell_count": len(flat)}
                out["got"]["95"] = True

            # assemble temps (0x96)
            st.temp_frames.clear()
            await client.write_gatt_char(WRITE_UUID, build_request(0x96), response=False)
            await asyncio.sleep(0.15)
            t_t_end = _now() + 2.5
            temps: List[int] = []
            while _now() < t_t_end:
                _ = split_frames(buf)
                # iterate frames in order by frame_no
                for fn in sorted(st.temp_frames.keys()):
                    fr = st.temp_frames[fn]
                    d = decode_96_temps(fr[4:12])
                    for tv in d["temps_c"]:
                        if tv is None:
                            continue
                        temps.append(int(tv))
                await asyncio.sleep(0.05)
                if st.temp_count and temps and len(temps) >= int(st.temp_count):
                    break
            if temps:
                if st.temp_count:
                    temps = temps[: int(st.temp_count)]
                out["status"]["temps_96"] = {"temps_c": temps, "temp_count": len(temps)}
                out["got"]["96"] = True

            try:
                await client.stop_notify(NOTIFY_UUID)
            except Exception:
                pass

            out["connected"] = bool(client.is_connected)
            return 0

    try:
        cached = await _ble_device_from_bluez_cache(args.address, args.adapter)
        dev: Any = cached
        if dev is None and args.scan_timeout > 0:
            try:
                dev = await BleakScanner.find_device_by_address(args.address, timeout=float(args.scan_timeout), adapter=args.adapter)
            except TypeError:
                dev = await BleakScanner.find_device_by_address(args.address, timeout=float(args.scan_timeout))
        client_arg: Any = dev if dev is not None else args.address
        # Overall safety timeout: connect + scanning can hang when BlueZ is unhappy.
        overall = float(args.timeout) + float(args.scan_timeout) + 10.0
        await asyncio.wait_for(run_once(BleakClient(client_arg, timeout=args.timeout, adapter=args.adapter)), timeout=overall)
        # success path prints JSON too
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except exc.BleakDeviceNotFoundError as e_nf:
        out["error"] = {"type": e_nf.__class__.__name__, "message": str(e_nf)}
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except Exception as e:
        out["error"] = {"type": e.__class__.__name__, "message": str(e)}
        if args.debug:
            out["traceback"] = traceback.format_exc()
        print(json.dumps(out, ensure_ascii=False))
        return 0
    finally:
        pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
