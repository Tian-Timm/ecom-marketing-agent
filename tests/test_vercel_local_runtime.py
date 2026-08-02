import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VercelLocalRuntimeContractTests(unittest.TestCase):
    def test_python_functions_avoid_local_builder_max_duration_metadata(self) -> None:
        """Vercel CLI 58 local Python builds reject builder maxDuration output."""
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))

        for pattern, options in config.get("functions", {}).items():
            if pattern.endswith(".py"):
                self.assertNotIn(
                    "maxDuration",
                    options,
                    "vercel dev currently returns HTTP 500 before Python starts when "
                    "a Python function build contains maxDuration metadata",
                )

    def test_python_api_entries_bootstrap_project_imports_without_loading_dotenv(self) -> None:
        for path in sorted((ROOT / "api").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            with self.subTest(api=path.name):
                bootstrap = source.find("sys.path.insert(0, str(root))")
                src_import = source.find("from src.web_api")
                self.assertGreaterEqual(bootstrap, 0)
                self.assertGreater(src_import, bootstrap)
                self.assertNotIn("local_environment", source)


if __name__ == "__main__":
    unittest.main()
