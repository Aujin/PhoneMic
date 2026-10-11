import queue
import unittest

from pcm_buffer import PcmBuffer


class PcmBufferTest(unittest.TestCase):
    def test_surplus_samples_are_kept_for_next_callback(self):
        pcm_queue = queue.Queue()
        pcm_queue.put(bytes(range(8)))
        pcm_queue.put(bytes(range(8, 16)))
        buffer = PcmBuffer(pcm_queue)

        self.assertEqual(buffer.pull(5), bytes(range(10)))
        self.assertEqual(buffer.pull(3), bytes(range(10, 16)))

    def test_short_read_is_padded_with_silence(self):
        pcm_queue = queue.Queue()
        pcm_queue.put(b"\x01\x02")
        buffer = PcmBuffer(pcm_queue)

        self.assertEqual(buffer.pull(2), b"\x01\x02\x00\x00")
        self.assertEqual(buffer.pull(1), b"\x00\x00")


if __name__ == "__main__":
    unittest.main()
