"""Keep decoded PCM samples across audio output callbacks."""

import queue


class PcmBuffer:
    def __init__(self, pcm_queue, channels=1, sample_width=2):
        self.pcm_queue = pcm_queue
        self.bytes_per_frame = channels * sample_width
        self.pending = bytearray()

    def pull(self, frames):
        needed = frames * self.bytes_per_frame
        while len(self.pending) < needed:
            try:
                self.pending.extend(self.pcm_queue.get_nowait())
            except queue.Empty:
                break

        result = bytes(self.pending[:needed])
        del self.pending[:needed]
        return result.ljust(needed, b"\x00")
