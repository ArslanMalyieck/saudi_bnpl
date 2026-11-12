import unittest
from pathlib import Path


class CompileTests(unittest.TestCase):
    def test_all_python_sources_compile_without_writing_bytecode(self):
        root = Path(__file__).resolve().parents[1]
        sources = list(root.rglob("*.py"))
        self.assertTrue(sources)
        for source in sources:
            with self.subTest(source=str(source.relative_to(root))):
                compile(source.read_bytes(), str(source), "exec")
