"""Keeps every bot in the hexagonal shape: checks imports, so a shortcut fails a test instead of
quietly coupling business logic to a technology.

  domain/          imports only the standard library and its own domain modules
  application.py   imports the domain, ports and shared core/ports, never an adapter
  ports.py         imports only the shared core/ports
  xbots/core, xbots/ports   import nothing from adapters
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STDLIB = set(sys.stdlib_module_names)


def imports(path):
    """(module, level) for every import in a file; level > 0 means a relative import."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(alias.name, 0) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append((node.module or "", node.level))
    return out


def bots():
    return [p for p in (ROOT / "bots").iterdir() if p.is_dir() and (p / "__init__.py").exists()]


class ArchitectureTests(unittest.TestCase):
    def test_domain_has_no_outside_dependencies(self):
        for bot in bots():
            for path in (bot / "domain").glob("*.py"):
                for module, level in imports(path):
                    with self.subTest(file=str(path.relative_to(ROOT)), imports=module):
                        if level == 1:
                            continue  # another module in the same domain package
                        self.assertEqual(level, 0, "domain may only import from inside domain/")
                        self.assertTrue(module.split(".")[0] in STDLIB,
                                        f"domain may only use the standard library, not {module!r}")

    def test_use_cases_never_import_adapters(self):
        for bot in bots():
            path = bot / "application.py"
            for module, level in imports(path):
                with self.subTest(file=str(path.relative_to(ROOT)), imports=module):
                    self.assertNotIn("adapters", module.split("."), "use cases depend on ports, not adapters")
                    if level == 0 and module.split(".")[0] not in STDLIB:
                        self.assertTrue(module.startswith(("xbots.ports", "xbots.core")),
                                        "only shared ports and core objects from xbots")

    def test_ports_only_use_shared_core_and_ports(self):
        paths = [b / "ports.py" for b in bots()] + list((ROOT / "xbots" / "ports").glob("*.py"))
        for path in paths:
            for module, level in imports(path):
                with self.subTest(file=str(path.relative_to(ROOT)), imports=module):
                    self.assertNotIn("adapters", module.split("."))
                    if level == 0 and module.split(".")[0] not in STDLIB:
                        self.assertTrue(module.startswith(("xbots.ports", "xbots.core")))

    def test_shared_core_is_pure(self):
        for path in (ROOT / "xbots" / "core").glob("*.py"):
            for module, level in imports(path):
                with self.subTest(file=str(path.relative_to(ROOT)), imports=module):
                    self.assertTrue(level == 1 or module.split(".")[0] in STDLIB)

    def test_every_registered_bot_is_complete(self):
        from xbots import registry
        for name in registry.names():
            with self.subTest(bot=name):
                bot = registry.load(name)
                self.assertEqual(bot.NAME, name)
                self.assertTrue(bot.DESCRIPTION)
                folder = Path(bot.__file__).parent
                for part in ("domain", "ports.py", "application.py", "adapters", "app.py", "cli.py"):
                    self.assertTrue((folder / part).exists(), f"{name} is missing {part}")
                workflow = ROOT / ".github" / "workflows" / f"{name}.yml"
                self.assertTrue(workflow.exists(), f"{name} has no workflow at {workflow.relative_to(ROOT)}")


if __name__ == "__main__":
    unittest.main()
