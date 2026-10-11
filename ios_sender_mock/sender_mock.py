"""PhoneMic mock sender.

Stands in for the iOS Flutter app. Generates a 1 kHz sine wave, encodes it
with Opus (via pyogg's bundled libopus), frames it, and ships it to the
Windows receiver over UDP on the LAN.

Wire format (v0, debug-friendly):
    [0]      type  = 0x10 (DATA)
    [1]      seq   (uint8, wraps)
    [2..3]   seq   (uint16, big-endian, full counter)
    [4..7]   timestamp (uint32 BE, samples since stream start @ 48kHz)
    [8..9]   payload length (uint16 BE)
    [10..]   Opus payload bytes

Run:
    python sender_mock.py --dest 192.168.1.20 --port 50000
"""
import argparse
import ctypes
import socket
import struct
import time

import numpy as np
from pyogg import opus as pyo

DEFAULT_SR = 48000
DEFAULT_BR = 64000
DEFAULT_FRAME_MS = 20
DEFAULT_CH = 1

DATA_TYPE = 0x10


def _bind_libopus():
    lib = pyo.libopus
    lib.opus_encoder_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_int)]
    lib.opus_encoder_create.restype = ctypes.c_void_p
    lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
    lib.opus_encoder_destroy.restype = None
    lib.opus_encoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    lib.opus_encoder_ctl.restype = ctypes.c_int
    lib.opus_encode.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int32,
    ]
    lib.opus_encode.restype = ctypes.c_int
    return lib


def make_encoder(lib, sr, ch, bitrate):
    err = ctypes.c_int()
    enc = lib.opus_encoder_create(sr, ch, pyo.OPUS_APPLICATION_AUDIO, ctypes.byref(err))
    if not enc or err.value != 0:
        raise RuntimeError(f"opus_encoder_create failed err={err.value}")
    lib.opus_encoder_ctl(enc, pyo.OPUS_SET_BITRATE_REQUEST, bitrate)
    return enc


def encode_frame(lib, enc, pcm_i16, frame_samples):
    pcm_buf = (ctypes.c_int16 * frame_samples)(*pcm_i16.tolist())
    out = (ctypes.c_ubyte * 4000)()
    n = lib.opus_encode(enc, pcm_buf, frame_samples, out, 4000)
    if n <= 0:
        raise RuntimeError(f"opus_encode failed: {n}")
    return bytes(out[:n])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True, help="receiver IP")
    ap.add_argument("--port", type=int, default=50000)
    ap.add_argument("--sr", type=int, default=DEFAULT_SR)
    ap.add_argument("--bitrate", type=int, default=DEFAULT_BR)
    ap.add_argument("--frame-ms", type=int, default=DEFAULT_FRAME_MS, choices=[10, 20, 40, 60])
    ap.add_argument("--freq", type=float, default=1000.0, help="sine frequency")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="stop after N seconds (0 = forever)")
    args = ap.parse_args()

    ch = DEFAULT_CH
    frame_samples = args.sr * args.frame_ms // 1000
    period_s = args.frame_ms / 1000.0

    lib = _bind_libopus()
    enc = make_encoder(lib, args.sr, ch, args.bitrate)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"[sender] -> {args.dest}:{args.port}  {args.sr}Hz {ch}ch "
          f"{args.bitrate/1000:.0f}kbps frame={args.frame_ms}ms "
          f"({frame_samples} samples)")

    seq = 0
    ts = 0
    started = time.monotonic()
    try:
        while True:
            t = np.arange(frame_samples, dtype=np.float32) / args.sr
            # phase-continuous across frames: use ts as base
            phase = (ts + np.arange(frame_samples)) / args.sr
            pcm = (np.sin(2 * np.pi * args.freq * phase) * 0.3 * 32767).astype(np.int16)

            payload = encode_frame(lib, enc, pcm, frame_samples)
            header = struct.pack("!BBHIH", DATA_TYPE, seq & 0xFF, seq & 0xFFFF,
                                 ts & 0xFFFFFFFF, len(payload))
            sock.sendto(header + payload, (args.dest, args.port))

            seq += 1
            ts += frame_samples

            next_at = started + seq * period_s
            now = time.monotonic()
            sleep_s = next_at - now
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                # running late, drop sleeps to catch up
                pass

            if args.seconds and (now - started) >= args.seconds:
                print(f"[sender] reached --seconds={args.seconds}, stopping")
                break
    except KeyboardInterrupt:
        print("\n[sender] interrupted")
    finally:
        lib.opus_encoder_destroy(enc)
        sock.close()


if __name__ == "__main__":
    main()

