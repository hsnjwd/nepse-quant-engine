# DEPRECATED — legacy ad-hoc test script (Sprint 10).
# Superseded by the real test suite in tests/. Remove in v1.1.
# TODO(v1.1): delete this file.
import sys
import unittest

import launcher_utils


class LauncherUtilsTests(unittest.TestCase):
    def test_python_executable_uses_current_interpreter(self):
        self.assertEqual(launcher_utils.get_python_executable(), sys.executable)


if __name__ == "__main__":
    unittest.main()
