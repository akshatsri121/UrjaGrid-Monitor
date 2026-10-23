"""Keep the hosting manifest explicit and identical to the tested lock."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DependencyManifestTests(unittest.TestCase):
    def test_root_has_explicit_pins_matching_lock(self):
        def entries(path):
            return [line.strip() for line in path.read_text(encoding='utf-8').splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]

        root_pins = entries(ROOT / 'requirements.txt')
        lock_pins = entries(ROOT / 'backend/requirements-control.lock.txt')
        self.assertTrue(root_pins)
        for pin in root_pins:
            self.assertRegex(pin, r'^[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+$',
                             'Hosting manifest must use explicit pins, not -r includes')
        self.assertEqual(sorted(root_pins), sorted(lock_pins))


if __name__ == '__main__':
    unittest.main()
