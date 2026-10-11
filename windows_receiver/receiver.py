"""PhoneMic Windows receiver.

Listens for Opus-encoded PCM frames over UDP (LAN), decodes them with the
libopus shipped in the pyogg wheel, and plays them out via sounddevice.
Auto-selects VB-Cable "CABLE Input" if present, otherwise falls back to
the default output device and logs install instructions.

Wire format (matches sender_mock.py v0):
    [0]      type   = 0x10
    [1]      seq8
    [2..3]   seq16 BE
    [4..7]   timestamp uint32 BE (samples @ 48 kHz)
    [8..9]   payload length uint16 BE
    [10..]   Opus bytes

Run:
    python receiver.py --port 50000
    python receiver.py --port 50000 --device "CABLE Input"
"""
import argparse
import ctypes
import queue
import socket
import struct
import sys
import threading
import time

import numpy as np
import sounddevice as sd
from pyogg import opus as pyo
from pcm_buffer import PcmBuffer

DATA_TYPE = 0x10
HEADER_FMT = "!BBHIH"
HEADER_LEN = struct.calcsize(HEADER_FMT)  # 10

VB_CABLE_HINT = "CABLE Input"
PAIR_DEFAULT = 4


def _bind_libopus():
    lib = pyo.libopus
    lib.opus_decoder_create.argtypes = [ctypes.c_int, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_int)]
    lib.opus_decoder_create.restype = ctypes.c_void_p
    lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
    lib.opus_decoder_destroy.restype = None
    lib.opus_decode.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_int,
        ctypes.c_int,
    ]
    lib.opus_decode.restype = ctypes.c_int
    return lib


class DecoderCache:
    """Reuse decoder across frames; rebuild on (sr, ch) change."""

    def __init__(self, lib):
        self.lib = lib
        self.key = None
        self.dec = None
        self.err = ctypes.c_int()

    def get(self, sr, ch, frame_samples):
        key = (sr, ch)
        if key != self.key:
            if self.dec:
                self.lib.opus_decoder_destroy(self.dec)
            self.dec = self.lib.opus_decoder_create(sr, ch, ctypes.byref(self.err))
            if not self.dec or self.err.value != 0:
                raise RuntimeError(f"opus_decoder_create err={self.err.value}")
            self.key = key
        return self.dec, frame_samples * ch

    def close(self):
        if self.dec:
            self.lib.opus_decoder_destroy(self.dec)
            self.dec = None


def find_output_device(name_hint):
    try:
        devs = sd.query_devices()
    except Exception as e:
        print(f"[receiver] sounddevice query_devices failed: {e}", file=sys.stderr)
        return None, None
    for idx, d in enumerate(devs):
        if d.get("max_output_channels", 0) <= 0:
            continue
        if name_hint.lower() in d["name"].lower():
            return idx, d
    return None, None


def list_outputs():
    print("[receiver] available output devices:")
    for idx, d in enumerate(sd.query_devices()):
        if d.get("max_output_channels", 0) > 0:
            print(f"  {idx:3d}  out={d['max_output_channels']}  {d['name']}")


def decode_packet(lib, dec_cache, pkt, sr, ch, frame_samples):
    if len(pkt) < HEADER_LEN:
        return None
    typ, _seq8, _seq16, _ts, plen = struct.unpack(HEADER_FMT, pkt[:HEADER_LEN])
    if typ != DATA_TYPE:
        return None
    if len(pkt) < HEADER_LEN + plen:
        return None
    payload = pkt[HEADER_LEN:HEADER_LEN + plen]
    dec, out_max = dec_cache.get(sr, ch, frame_samples)
    in_buf = (ctypes.c_ubyte * len(payload))(*payload)
    out_buf = (ctypes.c_int16 * out_max)()
    n = lib.opus_decode(dec, in_buf, len(payload), out_buf, frame_samples, 0)
    if n <= 0:
        return None
    return np.array(out_buf[: n * ch], dtype=np.int16)


def receiver_thread(sock, lib, dec_cache, pcm_q, stop_evt, sr, ch, frame_samples, log):
    last_log = 0.0
    frames = 0
    while not stop_evt.is_set():
        try:
            pkt, _addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        pcm = decode_packet(lib, dec_cache, pkt, sr, ch, frame_samples)
        if pcm is None:
            continue
        try:
            pcm_q.put_nowait(pcm.tobytes())
        except queue.Full:
            # drop oldest to keep latency bounded
            try:
                pcm_q.get_nowait()
                pcm_q.put_nowait(pcm.tobytes())
            except queue.Empty:
                pass
        frames += 1
        now = time.monotonic()
        if now - last_log >= 1.0:
            log(f"[receiver] {frames} frames, q={pcm_q.qsize()}")
            last_log = now
            frames = 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=50000)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--sr", type=int, default=48000)
    ap.add_argument("--bitrate", type=int, default=64000)
    ap.add_argument("--frame-ms", type=int, default=20, choices=[10, 20, 40, 60])
    ap.add_argument("--device", default=None,
                    help="output device name (auto-detect VB-Cable if omitted)")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--latency", type=float, default=0.05,
                    help="sounddevice blocksize in seconds")
    args = ap.parse_args()

    ch = 1
    frame_samples = args.sr * args.frame_ms // 1000
    if args.list_devices:
        list_outputs()
        return

    # device selection
    if args.device:
        dev_idx, dev = find_output_device(args.device)
        if dev is None:
            print(f"[receiver] WARN: device '{args.device}' not found, falling back")
            dev_idx, dev = None, None
    else:
        dev_idx, dev = find_output_device(VB_CABLE_HINT)
        if dev is None:
            print(f"[receiver] '{VB_CABLE_HINT}' not installed -- falling back to default")
            print("[receiver] install VB-Cable: https://vb-audio.com/Cable/ "
                  "then rerun and any app that records the 'CABLE Output' will see this stream")

    if dev:
        print(f"[receiver] using output device [{dev_idx}] {dev['name']}")
    else:
        print("[receiver] using default output device")

    lib = _bind_libopus()
    dec_cache = DecoderCache(lib)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.2)
    print(f"[receiver] listening on udp://{args.bind}:{args.port}  "
          f"{args.sr}Hz {ch}ch {args.bitrate/1000:.0f}kbps frame={args.frame_ms}ms")

    pcm_q: "queue.Queue[bytes]" = queue.Queue(maxsize=20)
    pcm_buffer = PcmBuffer(pcm_q, channels=ch)
    stop_evt = threading.Event()

    rx = threading.Thread(
        target=receiver_thread,
        args=(sock, lib, dec_cache, pcm_q, stop_evt, args.sr, ch, frame_samples, print),
        daemon=True,
    )
    rx.start()

    blocksize = max(64, int(args.sr * args.latency))
    def callback(outdata, frames, time_info, status):
        if status:
            print(f"[receiver] stream status: {status}", file=sys.stderr)
        outdata[:] = pcm_buffer.pull(frames)

    try:
        with sd.RawOutputStream(
            samplerate=args.sr,
            channels=ch,
            dtype="int16",
            blocksize=blocksize,
            device=(dev["name"] if dev else None),
            callback=callback,
        ):
            print(f"[receiver] playing. blocksize={blocksize} frames "
                  f"({args.latency*1000:.0f} ms target). Ctrl-C to stop.")
            while not stop_evt.is_set():
                time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[receiver] interrupted")
    finally:
        stop_evt.set()
        rx.join(timeout=1.0)
        dec_cache.close()
        sock.close()


if __name__ == "__main__":
    main()

