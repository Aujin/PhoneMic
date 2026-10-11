"""Smoke test: 1 kHz sine through libopus via pyogg's bundled opus.dll."""
import ctypes
import numpy as np
from pyogg import opus as pyo

lib = pyo.libopus
lib.opus_get_version_string.restype = ctypes.c_char_p
print("libopus version:", lib.opus_get_version_string().decode())

SR, CH, F = 48000, 1, 960
BR = 64000

lib.opus_encoder_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
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

err = ctypes.c_int()
enc = lib.opus_encoder_create(SR, CH, pyo.OPUS_APPLICATION_AUDIO, ctypes.byref(err))
assert enc and err.value == 0, f"encoder_create failed err={err.value}"

lib.opus_encoder_ctl(enc, pyo.OPUS_SET_BITRATE_REQUEST, BR)

t = np.arange(F, dtype=np.float32) / SR
pcm_i16 = (np.sin(2 * np.pi * 1000 * t) * 0.3 * 32767).astype(np.int16)
pcm_in = (ctypes.c_int16 * F)(*pcm_i16.tolist())
buf = (ctypes.c_ubyte * 4000)()
n = lib.opus_encode(enc, pcm_in, F, buf, 4000)
assert n > 0, f"encode failed: {n}"
print("encoded bytes:", n)
encoded = bytes(buf[:n])

lib.opus_decoder_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
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

dec = lib.opus_decoder_create(SR, CH, ctypes.byref(err))
assert dec and err.value == 0, f"decoder_create failed err={err.value}"

in_buf = (ctypes.c_ubyte * len(encoded))(*encoded)
out_buf = (ctypes.c_int16 * (F * 2))()
n_out = lib.opus_decode(dec, in_buf, len(encoded), out_buf, F * CH, 0)
assert n_out > 0, f"decode failed: {n_out}"
print("decoded samples:", n_out, "expected:", F)
out = np.array(out_buf[:F])
print("first 5 decoded:", out[:5], "last 5:", out[-5:])

lib.opus_encoder_destroy(enc)
lib.opus_decoder_destroy(dec)
print("SMOKE TEST OK")
