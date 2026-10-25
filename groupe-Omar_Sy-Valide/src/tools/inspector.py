"""Read a file and outline it with `ast` so prompts stay small (SPEC §4.2)."""

import ast
from pathlib import Path


def _first_line(node: ast.AST) -> str | None:
    if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        doc = ast.get_docstring(node)
        if doc:
            return f'"""{doc.splitlines()[0]}"""'
    return None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"


class CodeInspector:
    def read_file(self, path: Path) -> str:
        return Path(path).read_text(encoding="utf-8")

    def inspect_ast(self, path: Path) -> str:
        """Classes, function signatures and first docstring lines; no function bodies."""
        try:
            tree = ast.parse(self.read_file(path))
        except SyntaxError as exc:
            return f"SyntaxError: {exc.msg} (line {exc.lineno})"
        lines: list[str] = []
        if doc := _first_line(tree):
            lines.append(doc)
        self._outline(tree.body, 0, lines)
        return "\n".join(lines)

    def _outline(self, body: list[ast.stmt], depth: int, lines: list[str]) -> None:
        indent = "    " * depth
        for node in body:
            if isinstance(node, ast.ClassDef):
                bases = ", ".join(ast.unparse(b) for b in node.bases)
                lines.append(
                    f"{indent}class {node.name}({bases}):" if bases else f"{indent}class {node.name}:"
                )
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                lines.append(indent + _signature(node))
            else:
                continue
            if doc := _first_line(node):
                lines.append(f"{indent}    {doc}")
            if isinstance(node, ast.ClassDef):
                self._outline(node.body, depth + 1, lines)
