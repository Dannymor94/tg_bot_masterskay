import ast
from pathlib import Path


def test_domain_has_no_io_imports():
    forbidden = {"httpx", "aiogram", "sqlalchemy"}
    for path in Path("app/domain").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not forbidden.intersection(name.split(".")[0] for name in names), path
