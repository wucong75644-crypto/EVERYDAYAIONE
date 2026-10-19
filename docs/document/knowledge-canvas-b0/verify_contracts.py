"""Validate B0 artifacts only; never opens a database or calls external services."""

import json
import re
import subprocess
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

HERE = Path(__file__).resolve().parent
spec = json.loads((HERE / "contracts.json").read_text())
schemas = spec["components"]["schemas"]


def walk(value):
    if isinstance(value, dict):
        if "$ref" in value:
            pointer = value["$ref"]
            assert pointer.startswith("#/"), pointer
            target = spec
            for part in pointer[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
        for item in value.values():
            walk(item)
    elif isinstance(value, list):
        for item in value:
            walk(item)


walk(spec)
for name, schema in schemas.items():
    Draft202012Validator.check_schema(schema)
for case in spec["x-contract-tests"]:
    root = {
        "$ref": "#/components/schemas/" + case["schema"],
        "components": spec["components"],
    }
    errors = list(
        Draft202012Validator(root, format_checker=FormatChecker()).iter_errors(
            case["value"]
        )
    )
    assert (not errors) == case["valid"], (case["name"], [e.message for e in errors])

tools = spec["x-model-tools"]
assert len({t["name"] for t in tools}) == 4
for path, methods in spec["paths"].items():
    for operation in methods.values():
        assert operation["security"] == [{"BearerAuth": []}], path
        assert "404" in operation["responses"], path
        for name in re.findall(r"{([^}]+)}", path):
            assert any(
                p.get("name") == name and p.get("in") == "path" and p.get("required")
                for p in operation.get("parameters", [])
            ), (path, name)

db = json.loads((HERE / "schema-contract.json").read_text())
tables = {t["name"]: t for t in db["tables"]}
assert len(tables) == len(db["tables"])
external = {
    "users", "organizations", "messages", "conversations",
    "scheduled_tasks", "scheduled_task_runs", "user_assets",
}
for name, table in tables.items():
    cols = table["columns"]
    for group in [table["primary_key"], *table["unique"], *table["indexes"]]:
        assert set(group) <= cols.keys(), (name, group)
    assert table["row_level_security"]["enabled"] is True, name
    assert table["row_level_security"]["force"] is True, name
    assert table["row_level_security"]["select"], name
    for fk in table["foreign_keys"]:
        assert set(fk["columns"]) <= cols.keys(), (name, fk)
        target = fk["target_table"]
        assert target in tables or target in external, (name, target)
        assert len(fk["columns"]) == len(fk["target_columns"]), (name, fk)
        if target in tables:
            assert set(fk["target_columns"]) <= tables[target]["columns"].keys()
            keys = [tables[target]["primary_key"], *tables[target]["unique"]]
            assert fk["target_columns"] in keys, (name, "non-unique FK target", fk)

main = HERE.parent / "TECH_统一知识画布实施方案.md"
for doc in [main, HERE.parent / "TECH_统一知识画布_B0契约.md"]:
    text = doc.read_text()
    assert sum(line.startswith("```") for line in text.splitlines()) % 2 == 0, doc
    for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
        if not link.startswith(("http://", "https://", "#")):
            assert (doc.parent / link.split("#")[0]).exists(), (doc, link)
assert re.findall(r"^\| (KB-R\d{2}) \|", main.read_text(), re.M) == [
    f"KB-R{i:02}" for i in range(1, 19)
]
html = (HERE / "prototype.html").read_text()
scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
assert len(scripts) == 1
subprocess.run(["node", "--check", "-"], input=scripts[0], text=True, check=True)
subprocess.run(["git", "diff", "--check"], cwd=HERE, check=True)
print(
    f"PASS: {len(schemas)} schemas, {len(spec['x-contract-tests'])} schema examples, "
    f"{len(spec['paths'])} API paths, {len(tools)} tools, {len(tables)} table contracts; "
    "references and prototype JavaScript syntax"
)
