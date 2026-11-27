"""What the Mac's hardware is doing, read without root: DRAM bandwidth and the GPU's busy share and
clock, from IOReport (the private framework Activity Monitor and powermetrics use).

- `MemBW.read()`: average and 95th-percentile DRAM bandwidth (GB/s), read, write and total,
  since the previous read. The power manager's "PMP" / "DCS BW" channels are histograms of
  how long traffic spent in each band (labels such as "32GB/s", each 32 GB/s wide); the
  time-weighted mean of band midpoints is the average. Per-agent byte counters ("AMC Stats")
  need root, so this says how much moved, not who moved it.
- `GPUState.read()`: the share of time the GPU was in an active performance state ("GPU Stats"
  / "GPU Performance States" / GPUPH), the average clock while active (each state's clock is
  in the power manager's table, `voltage-states9`), and the FP16/FP32 ceiling that clock allows:
  cores x 128 lanes x 2 ops per fused multiply-add. Apple's GPU runs FP16 at the FP32 rate and
  has no faster integer matrix path; llama.cpp dequantizes weights to FP16 in its kernels, so
  its work is counted in FLOPs, not integer TOPS.

- `SystemPower.read()`: the whole Mac's watts from the SMC (the System Management Controller,
  read through AppleSMC without root): `PSTR` (system total) on a laptop, else `PD0R`, the
  power supply's 12 V output on a Mac Studio (about 23 W idle on an M5 Ultra; its `Pb0f` agrees,
  12.09 V x 1.82 A). Wall power is a little more, the supply's own loss.

Per-part watts (CPU, GPU, DRAM, ANE) are not read: IOReport's "Energy Model" counters, in mJ, stay
put unless powermetrics (root) runs, and the one that moves, "GPU Energy" in nJ, read 1.7 W for a
GPU at 100% while the Mac Studio drew 97 W more than idle (2026-11-27): only part of the GPU.

An M5 Ultra has a power manager per die: bandwidth comes from "PMP0" and "PMP1" (one AMCC each,
`AMCC0 RD`, `AMCC1 RD`), summed; a single-die Mac has "PMP" (`AMCC RD`).

All raise OSError where IOReport or the SMC is missing or refuses (not a Mac, or a new macOS),
each on its own, so a caller can go without any of them.
"""
from __future__ import annotations

import ctypes as C
import re
import struct
import subprocess

_UTF8 = 0x08000100
_BAND = re.compile(r"([\d.]+)\s*GB/s")
_LANES_PER_CORE = 128


class _IOReport:
    """The few IOReport and CoreFoundation calls used here, loaded once."""

    def __init__(self) -> None:
        try:
            cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            io = C.CDLL("/usr/lib/libIOReport.dylib")
        except OSError as e:
            raise OSError(f"IOReport is not available: {e}") from e
        vp, i64 = C.c_void_p, C.c_int64
        for name, res, args in [
            ("CFStringCreateWithCString", vp, [vp, C.c_char_p, C.c_uint32]),
            ("CFStringGetCString", C.c_bool, [vp, C.c_char_p, C.c_long, C.c_uint32]),
            ("CFDictionaryGetValue", vp, [vp, vp]),
            ("CFArrayGetCount", C.c_long, [vp]),
            ("CFArrayGetValueAtIndex", vp, [vp, C.c_long]),
            ("CFRelease", None, [vp]),
        ]:
            f = getattr(cf, name)
            f.restype, f.argtypes = res, args
        for name, res, args in [
            ("IOReportCopyChannelsInGroup", vp, [vp, vp, i64, i64, i64]),
            ("IOReportCreateSubscription", vp, [vp, vp, C.POINTER(vp), i64, vp]),
            ("IOReportCreateSamples", vp, [vp, vp, vp]),
            ("IOReportCreateSamplesDelta", vp, [vp, vp, vp]),
            ("IOReportChannelGetChannelName", vp, [vp]),
            ("IOReportChannelGetFormat", C.c_int32, [vp]),
            ("IOReportStateGetCount", C.c_int32, [vp]),
            ("IOReportStateGetNameForIndex", vp, [vp, C.c_int32]),
            ("IOReportStateGetResidency", i64, [vp, C.c_int32]),
        ]:
            f = getattr(io, name)
            f.restype, f.argtypes = res, args
        self.cf, self.io = cf, io
        self.channels_key = self.cfstr("IOReportChannels")

    def cfstr(self, text: str):
        return self.cf.CFStringCreateWithCString(None, text.encode(), _UTF8)

    def pystr(self, ref) -> str:
        if not ref:
            return ""
        buf = C.create_string_buffer(256)
        return buf.value.decode() if self.cf.CFStringGetCString(ref, buf, 256, _UTF8) else ""


_lib: _IOReport | None = None


def _ioreport() -> _IOReport:
    global _lib
    if _lib is None:
        _lib = _IOReport()
    return _lib


class _Subscription:
    """One IOReport group/subgroup; `states()` yields (channel, [(state, residency)]) since last."""

    def __init__(self, group: str, subgroup: str | None) -> None:
        self.lib = lib = _ioreport()
        chans = lib.io.IOReportCopyChannelsInGroup(lib.cfstr(group), lib.cfstr(subgroup) if subgroup else None,
                                                   0, 0, 0)
        if not chans:
            raise OSError(f"IOReport has no {group} / {subgroup}")
        self.sub = C.c_void_p()
        self.subscription = lib.io.IOReportCreateSubscription(None, chans, C.byref(self.sub), 0, None)
        if not self.subscription:
            raise OSError(f"IOReport refused {group} / {subgroup}")
        self.last = lib.io.IOReportCreateSamples(self.subscription, self.sub, None)

    def states(self):
        """(channel, [(state, residency)]) for each state-residency channel, since the last call."""
        for ch, name in self._delta(2):
            lib = self.lib
            yield name, [(lib.pystr(lib.io.IOReportStateGetNameForIndex(ch, j)).strip(),
                          lib.io.IOReportStateGetResidency(ch, j))
                         for j in range(lib.io.IOReportStateGetCount(ch))]

    def _delta(self, fmt: int):
        lib = self.lib
        now = lib.io.IOReportCreateSamples(self.subscription, self.sub, None)
        delta = lib.io.IOReportCreateSamplesDelta(self.last, now, None)
        lib.cf.CFRelease(self.last)
        self.last = now
        try:
            arr = lib.cf.CFDictionaryGetValue(delta, lib.channels_key)
            for i in range(lib.cf.CFArrayGetCount(arr) if arr else 0):
                ch = lib.cf.CFArrayGetValueAtIndex(arr, i)
                if lib.io.IOReportChannelGetFormat(ch) != fmt:  # 2: state residencies
                    continue
                yield ch, lib.pystr(lib.io.IOReportChannelGetChannelName(ch)).strip()
        finally:
            lib.cf.CFRelease(delta)


def bandwidth(states: list[tuple[str, int]]) -> dict | None:
    """Average and p95 GB/s from a DCS BW histogram, or None if no time was recorded."""
    bands = sorted((float(m.group(1)), r) for name, r in states if (m := _BAND.search(name)))
    total = sum(r for _, r in bands)
    if not total:
        return None
    width = bands[1][0] - bands[0][0] if len(bands) > 1 else bands[0][0]
    seen, p95 = 0, bands[-1][0]
    for top, r in bands:
        seen += r
        if seen >= 0.95 * total:
            p95 = top
            break
    return {"avg_gbs": round(sum((top - width / 2) * r for top, r in bands) / total, 1), "p95_gbs": p95}


_AMCC = re.compile(r"AMCC\d* (RD|WR|RD\+WR)")


def add_bandwidth(parts: list[dict]) -> dict | None:
    """One die's figures added to another's: averages add up, and so (as a bound) do the p95s."""
    if not parts:
        return None
    return {"avg_gbs": round(sum(p["avg_gbs"] for p in parts), 1), "p95_gbs": sum(p["p95_gbs"] for p in parts)}


class MemBW:
    KEYS = {"RD": "read", "WR": "write", "RD+WR": "total"}
    GROUPS = ("PMP", "PMP0", "PMP1", "PMP2", "PMP3")

    def __init__(self) -> None:
        self.subs = []
        for group in self.GROUPS:
            try:
                self.subs.append(_Subscription(group, "DCS BW"))
            except OSError:
                pass
        if not self.subs:
            raise OSError("IOReport refused every PMP / DCS BW")

    def read(self) -> dict:
        parts: dict[str, list[dict]] = {}
        for sub in self.subs:
            for name, states in sub.states():
                m = _AMCC.fullmatch(name)
                if m and (bw := bandwidth(states)):
                    parts.setdefault(self.KEYS[m.group(1)], []).append(bw)
        return {k: add_bandwidth(v) for k, v in parts.items()}


def gpu_state(states: list[tuple[str, int]], clocks_mhz: list[float], cores: int) -> dict | None:
    """Busy share, average active clock and the FLOPS ceiling from GPUPH residencies."""
    total = sum(r for _, r in states)
    if not total:
        return None
    active = mhz_time = 0
    for name, r in states:
        m = re.fullmatch(r"P(\d+)", name)
        if m and r:
            k = int(m.group(1))
            active += r
            mhz_time += (clocks_mhz[k] if k < len(clocks_mhz) else 0) * r
    mhz = mhz_time / active if active else 0.0
    peak = cores * _LANES_PER_CORE * 2 * mhz * 1e6 / 1e12
    return {"active": round(active / total, 3), "mhz": round(mhz), "ceiling_tflops": round(peak, 1),
            "busy_ceiling_tflops": round(peak * active / total, 1)}


def gpu_clocks_mhz() -> list[float]:
    """Each GPU performance state's clock, index = state number, from the power manager."""
    out = subprocess.run(["ioreg", "-l", "-w0"], capture_output=True).stdout.decode("utf8", "replace")
    m = re.search(r'"voltage-states9" = <([0-9a-f]+)>', out)
    raw = bytes.fromhex(m.group(1)) if m else b""
    return [struct.unpack("<II", raw[i:i + 8])[0] / 1e6 for i in range(0, len(raw) - 7, 8)]


def gpu_cores() -> int:
    out = subprocess.run(["system_profiler", "SPDisplaysDataType"], capture_output=True, text=True).stdout
    m = re.search(r"Total Number of Cores: (\d+)", out)
    return int(m.group(1)) if m else 0


class GPUState:
    def __init__(self) -> None:
        self.clocks, self.cores = gpu_clocks_mhz(), gpu_cores()
        if not self.clocks or not self.cores:
            raise OSError("no GPU clock table or core count")
        self.sub = _Subscription("GPU Stats", "GPU Performance States")

    def read(self) -> dict:
        for name, states in self.sub.states():
            if name == "GPUPH":
                return gpu_state(states, self.clocks, self.cores) or {}
        return {}


class _SMCKeyData(C.Structure):
    """AppleSMC's call structure (SMCKeyData_t, 80 bytes)."""
    class Vers(C.Structure):
        _fields_ = [("major", C.c_uint8), ("minor", C.c_uint8), ("build", C.c_uint8), ("reserved", C.c_uint8),
                    ("release", C.c_uint16)]

    class PLimit(C.Structure):
        _fields_ = [("version", C.c_uint16), ("length", C.c_uint16), ("cpu", C.c_uint32), ("gpu", C.c_uint32),
                    ("mem", C.c_uint32)]

    class KeyInfo(C.Structure):
        _fields_ = [("dataSize", C.c_uint32), ("dataType", C.c_uint32), ("dataAttributes", C.c_uint8)]

    _fields_ = [("key", C.c_uint32), ("vers", Vers), ("pLimitData", PLimit), ("keyInfo", KeyInfo),
                ("result", C.c_uint8), ("status", C.c_uint8), ("data8", C.c_uint8), ("data32", C.c_uint32),
                ("bytes", C.c_uint8 * 32)]


class SMC:
    """Read float keys from the System Management Controller, without root."""
    GET_KEY_INFO, READ_KEY = 9, 5

    def __init__(self) -> None:
        try:
            io = C.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
            libc = C.CDLL("/usr/lib/libSystem.B.dylib")
        except OSError as e:
            raise OSError(f"IOKit is not available: {e}") from e
        io.IOServiceMatching.restype, io.IOServiceMatching.argtypes = C.c_void_p, [C.c_char_p]
        io.IOServiceGetMatchingService.restype = C.c_uint32
        io.IOServiceGetMatchingService.argtypes = [C.c_uint32, C.c_void_p]
        io.IOServiceOpen.argtypes = [C.c_uint32, C.c_uint32, C.c_uint32, C.POINTER(C.c_uint32)]
        io.IOConnectCallStructMethod.argtypes = [C.c_uint32, C.c_uint32, C.c_void_p, C.c_size_t, C.c_void_p,
                                                 C.POINTER(C.c_size_t)]
        self.io, self.conn = io, C.c_uint32()
        service = io.IOServiceGetMatchingService(0, io.IOServiceMatching(b"AppleSMC"))
        task = C.c_uint32.in_dll(libc, "mach_task_self_").value
        if not service or io.IOServiceOpen(service, task, 0, C.byref(self.conn)):
            raise OSError("AppleSMC refused")

    def _call(self, inp: _SMCKeyData) -> _SMCKeyData | None:
        out, size = _SMCKeyData(), C.c_size_t(C.sizeof(_SMCKeyData))
        kr = self.io.IOConnectCallStructMethod(self.conn.value, 2, C.byref(inp), C.sizeof(_SMCKeyData),
                                               C.byref(out), C.byref(size))
        return None if kr or out.result else out

    def float(self, key: str) -> float | None:
        code = struct.unpack(">I", key.encode())[0]
        info = self._call(_SMCKeyData(key=code, data8=self.GET_KEY_INFO))
        if info is None or struct.pack(">I", info.keyInfo.dataType) != b"flt " or info.keyInfo.dataSize != 4:
            return None
        inp = _SMCKeyData(key=code, data8=self.READ_KEY)
        inp.keyInfo.dataSize = 4
        out = self._call(inp)
        return struct.unpack("<f", bytes(out.bytes[:4]))[0] if out else None


class SystemPower:
    #: (key, what it measures), first with a reading wins.
    KEYS = (("PSTR", "system total"), ("PD0R", "power supply output"))

    def __init__(self) -> None:
        self.smc = SMC()

    def read(self) -> dict:
        for key, source in self.KEYS:
            w = self.smc.float(key)
            if w and w > 0.05:
                return {"system_w": round(w, 1), "source": source}
        return {}


if __name__ == "__main__":
    import time
    readers = {}
    for name, make in (("dram", MemBW), ("gpu", GPUState), ("system", SystemPower)):
        try:
            readers[name] = make()
        except OSError as e:
            print(name, "unavailable:", e)
    for _ in range(3):
        time.sleep(1)
        print({name: r.read() for name, r in readers.items()})
