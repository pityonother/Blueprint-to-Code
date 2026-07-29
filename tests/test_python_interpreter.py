import tempfile
import unittest
from pathlib import Path

from _python_interpreter import compatible_python_interpreters, preferred_python


class PythonInterpreterSelectionTests(unittest.TestCase):
    def test_non_windows_never_selects_the_bundled_windows_executable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled = root / "runtime" / "python" / "python.exe"
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"windows executable fixture")
            current = Path("/usr/bin/python3")

            self.assertEqual(
                preferred_python(root, os_name="posix", current_python=current),
                current,
            )
            self.assertEqual(
                compatible_python_interpreters(
                    root,
                    os_name="posix",
                    current_python=current,
                ),
                (current,),
            )

    def test_windows_prefers_and_also_exercises_the_bundled_executable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled = root / "runtime" / "python" / "python.exe"
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"windows executable fixture")
            current = root / "current-python.exe"

            self.assertEqual(
                preferred_python(root, os_name="nt", current_python=current),
                bundled,
            )
            self.assertEqual(
                compatible_python_interpreters(
                    root,
                    os_name="nt",
                    current_python=current,
                ),
                (current, bundled),
            )


if __name__ == "__main__":
    unittest.main()
