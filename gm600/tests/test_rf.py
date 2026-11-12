"""GM600 tests: rf."""
from _common import *  # noqa: F401,F403


class CtcssTxTest(unittest.TestCase):
    """ctcss_tx: the HackRF IQ demodulates to the requested PL tone and audio, and nothing else."""

    def test_tone_and_no_tone(self):
        try:
            import ctcss_tx
        except ImportError as e:                       # numpy missing
            self.skipTest(str(e))
        dev = ctcss_tx.demod_check(ctcss_tx.make_iq(tone=123.0))
        self.assertAlmostEqual(dev(123.0), 500, delta=25)
        self.assertAlmostEqual(dev(1000), 2500, delta=50)
        self.assertLess(dev(200), 5)
        dev = ctcss_tx.demod_check(ctcss_tx.make_iq(tone=None))
        self.assertLess(dev(123.0), 5)
        self.assertEqual(len(ctcss_tx.make_iq()), 2 * ctcss_tx.FS)


if __name__ == "__main__":
    unittest.main()
