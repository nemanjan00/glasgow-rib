"""GM600 tests: ui."""
from _common import *  # noqa: F401,F403


class VmBridgeWriteRangeTest(unittest.TestCase):
    """gm600_vm_bridge's 0x17 must reject writes outside the 640-byte EEPROM (no grow, no wrap)."""

    @staticmethod
    def _bridge():
        import gm600_vm_bridge as vb
        r = vb.VMRadio.__new__(vb.VMRadio)            # skip the (slow) firmware boot; only test 0x17
        r.base = 0x0D80
        r.lock = threading.Lock()
        r.m = types.SimpleNamespace(eeprom=bytearray(640))
        return r

    def test_in_range_write_ok(self):
        r = self._bridge()
        reply = r.answer(encode(0x17, (0x0D80).to_bytes(3, "big") + b"\xAA\xBB"))
        self.assertEqual(r.m.eeprom[:2], b"\xAA\xBB")
        self.assertEqual(len(r.m.eeprom), 640)
        self.assertEqual(decode(reply[1:])[0], 0x84)

    def test_high_write_does_not_grow_image(self):
        r = self._bridge()
        reply = r.answer(encode(0x17, (0x2000).to_bytes(3, "big") + b"\x01"))   # ramdump stub addr
        self.assertEqual(len(r.m.eeprom), 640)
        self.assertEqual(decode(reply[1:])[0], 0x85)

    def test_low_write_does_not_wrap(self):
        r = self._bridge()
        reply = r.answer(encode(0x17, (0x0000).to_bytes(3, "big") + b"\x01"))
        self.assertEqual(len(r.m.eeprom), 640)
        self.assertNotEqual(r.m.eeprom[-1], 0x01)      # a negative offset must not write from the end
        self.assertEqual(decode(reply[1:])[0], 0x85)


if __name__ == "__main__":
    unittest.main()
