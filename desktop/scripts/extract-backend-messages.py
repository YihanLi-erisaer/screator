"""Collect application-owned desktop messages without translating stored content.

Run after changing backend copy. Dynamic values become numbered placeholders;
third-party responses and user-supplied content deliberately stay outside the catalog.
"""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "desktop/src/backend-source.json"
HAN = re.compile(r"[\u3400-\u9fff]")


def message(node):
    params = 0

    def render(value):
        nonlocal params
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        if isinstance(value, ast.JoinedStr):
            return "".join(render(part) for part in value.values)
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
            return render(value.left) + render(value.right)
        placeholder = "{p%d}" % params
        params += 1
        return placeholder

    result = render(node)
    return re.sub(r"\s+", " ", result).strip() if HAN.search(result) else None


def collect():
    phrases = set()

    def add(node):
        if isinstance(node, ast.IfExp):
            add(node.body)
            add(node.orelse)
        else:
            value = message(node)
            if value:
                phrases.add(value)

    for path in (ROOT / "screator").rglob("*.py"):
        # CLI-only commands and LLM prompts are not desktop UI copy.
        if path.name in {"cli.py", "cli_settings.py", "prompts.py", "translate.py", "desktop_selftest.py", "uninstall.py"}:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                for arg in node.exc.args:
                    add(arg)
            elif isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg in {"message", "error", "warning", "review_status"}:
                        add(keyword.value)
                if isinstance(node.func, ast.Attribute) and node.func.attr == "append" and "migration_notes" in ast.unparse(node.func.value):
                    for arg in node.args:
                        add(arg)
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value in {"message", "error", "warning", "review_status"}:
                        add(value)
            elif isinstance(node, ast.Assign):
                if any((isinstance(target, ast.Attribute) and target.attr == "error") or (isinstance(target, ast.Name) and target.id in {"message", "warning", "version"}) or (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) and target.slice.value == "review_status") for target in node.targets):
                    add(node.value)
    # Native transport errors cross the same UI boundary.
    native = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
    for value in re.findall(r'"([^"\n]*[\u3400-\u9fff][^"\n]*)"', native):
        phrases.add(re.sub(r"\{[^{}]+\}", "{p0}", value))
    return sorted(phrases)


def serialized():
    return json.dumps(collect(), ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    import sys
    output = serialized()
    if "--check" in sys.argv:
        if not DESTINATION.exists() or DESTINATION.read_text(encoding="utf-8") != output:
            sys.exit("Backend message catalog is stale. Run python desktop/scripts/extract-backend-messages.py.")
    else:
        DESTINATION.write_text(output, encoding="utf-8")
