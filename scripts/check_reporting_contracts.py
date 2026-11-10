"""Check reporting SQL against the read contracts in business-module documentation."""

import argparse
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = "docs/adr/0026-business-ownership-and-reporting-reads.md"
TABLE = re.compile(r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")
TOKEN = re.compile(
    r"/\*[\s\S]*?\*/|--[^\r\n]*|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|"
    r"[a-zA-Z_][\w$]*|[.,();]|\S"
)
IDENTIFIER = re.compile(r'(?:[a-zA-Z_][\w$]*|"(?:""|[^"])+")\Z')
FROM_END = {
    "WHERE",
    "GROUP",
    "ORDER",
    "HAVING",
    "LIMIT",
    "OFFSET",
    "FETCH",
    "UNION",
    "EXCEPT",
    "INTERSECT",
    "RETURNING",
    "SET",
    "VALUES",
    "WINDOW",
    "FOR",
    "QUALIFY",
}


def sql_strings(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assigned: dict[str, ast.expr] = {}
    text_names = {"text"}
    sqlalchemy_names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("sqlalchemy")
        ):
            for alias in node.names:
                if alias.name == "text":
                    text_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("sqlalchemy"):
                    sqlalchemy_names.add(alias.asname or alias.name.split(".", 1)[0])
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            assigned[node.target.id] = node.value

    def static_sql(node: ast.expr, seen: frozenset[str] = frozenset()) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
        if isinstance(node, ast.Name) and node.id in assigned and node.id not in seen:
            return static_sql(assigned[node.id], seen | {node.id})
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = static_sql(node.left, seen)
            right = static_sql(node.right, seen)
            return left + right if left is not None and right is not None else None
        return None

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        is_text = isinstance(node.func, ast.Name) and node.func.id in text_names
        is_sqlalchemy_text = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "text"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in sqlalchemy_names
        )
        if not (is_text or is_sqlalchemy_text) or not node.args:
            continue
        static = static_sql(node.args[0])
        if static is not None:
            found.append((node.lineno, static))
    return found


def contract_rows(readme: Path) -> dict[str, tuple[str, set[str]]]:
    text = readme.read_text(encoding="utf-8")
    lines = text.splitlines()
    header_index = next(
        (
            i
            for i, line in enumerate(lines)
            if TABLE.match(line) and "Owner" in line and "Table" in line
        ),
        None,
    )
    if header_index is None:
        return {}
    rows: dict[str, tuple[str, set[str]]] = {}
    for line in lines[header_index + 2 :]:
        match = TABLE.match(line)
        if not match:
            break
        owner, table, columns, _meaning = (part.strip() for part in match.groups())
        table = table.strip("`")
        if "." not in table:
            continue
        rows[table] = (owner, {column.strip().strip("`") for column in columns.split(",")})
    return rows


def _tokens(sql: str) -> list[str]:
    return [token for token in TOKEN.findall(sql) if not token.startswith(("'", "--", "/*"))]


def _qualified_at(tokens: list[str], index: int) -> tuple[str, int] | None:
    if index < len(tokens) and tokens[index].upper() == "ONLY":
        index += 1
    if (
        index + 2 < len(tokens)
        and IDENTIFIER.fullmatch(tokens[index])
        and tokens[index + 1] == "."
        and IDENTIFIER.fullmatch(tokens[index + 2])
    ):
        schema, table = tokens[index], tokens[index + 2]
        return f"{_identifier_value(schema)}.{_identifier_value(table)}", index + 3
    return None


def _identifier_value(name: str) -> str:
    return name[1:-1].replace('""', '"') if name.startswith('"') else name


def relation_refs(sql: str) -> list[str]:
    tokens = _tokens(sql)
    refs: list[str] = []
    active_from: set[int] = set()
    depth = 0
    for index, token in enumerate(tokens):
        upper = token.upper()
        if token == ")":
            active_from.discard(depth)
            depth = max(0, depth - 1)
            continue
        if upper in {"FROM", "JOIN"}:
            if upper == "FROM":
                active_from.add(depth)
            target = _qualified_at(tokens, index + 1)
            if target:
                refs.append(target[0])
        elif depth in active_from and upper in FROM_END:
            active_from.discard(depth)
        elif token == "," and depth in active_from:
            target = _qualified_at(tokens, index + 1)
            if target:
                refs.append(target[0])
        if token == "(":
            depth += 1
    return refs


def write_targets(sql: str) -> list[str]:
    tokens = _tokens(sql)
    targets: list[str] = []
    for index, token in enumerate(tokens):
        upper = token.upper()
        target_index: int | None = None
        if upper == "INSERT" and index + 1 < len(tokens) and tokens[index + 1].upper() == "INTO":
            target_index = index + 2
        elif upper == "UPDATE":
            target_index = index + 1
        elif (
            upper in {"DELETE", "MERGE"}
            and index + 1 < len(tokens)
            and tokens[index + 1].upper() == ("FROM" if upper == "DELETE" else "INTO")
        ):
            target_index = index + 2
        elif upper in {"COPY", "TRUNCATE"}:
            target_index = index + 1
            if target_index < len(tokens) and tokens[target_index].upper() == "TABLE":
                target_index += 1
        elif upper in {"DROP", "ALTER", "CREATE"}:
            cursor = index + 1
            if upper == "CREATE":
                while cursor < len(tokens) and tokens[cursor].upper() in {
                    "TEMP",
                    "TEMPORARY",
                    "UNLOGGED",
                    "GLOBAL",
                    "LOCAL",
                }:
                    cursor += 1
            if cursor < len(tokens) and tokens[cursor].upper() == "TABLE":
                cursor += 1
                if (
                    cursor + 1 < len(tokens)
                    and tokens[cursor].upper() == "IF"
                    and tokens[cursor + 1].upper() in {"EXISTS", "NOT"}
                ):
                    cursor += 2
                    if cursor < len(tokens) and tokens[cursor].upper() == "EXISTS":
                        cursor += 1
                target_index = cursor
        if target_index is None:
            continue
        target = _qualified_at(tokens, target_index)
        if target:
            targets.append(target[0])
            cursor = target[1]
            if upper in {"TRUNCATE", "DROP"}:
                while cursor < len(tokens) and tokens[cursor] == ",":
                    next_target = _qualified_at(tokens, cursor + 1)
                    if not next_target:
                        break
                    targets.append(next_target[0])
                    cursor = next_target[1]
    return targets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    root = parser.parse_args().root.resolve()
    failures: list[str] = []
    app_root = root / "backend/app"
    repositories = [
        *app_root.glob("modules/*/**/repository.py"),
        *app_root.glob("core/*/**/repository.py"),
    ]
    for repository in sorted(repositories):
        group = "modules" if "modules" in repository.parts else "core"
        module = repository.parts[repository.parts.index(group) + 1]
        owned_schema = module
        readme = app_root / group / module / "README.md"
        contracts = contract_rows(readme) if readme.exists() else {}
        relative = repository.relative_to(root)
        for line, raw_sql in sql_strings(repository):
            sql = raw_sql
            declared = set(contracts)
            for ref in relation_refs(sql):
                normalized = ref
                schema, _table = normalized.split(".", 1)
                if schema != owned_schema and normalized not in declared:
                    failures.append(
                        f"{relative}:{line}: reporting-read-contract: {normalized} is not declared "
                        f"in {readme.relative_to(root)}; add owner, table, columns and meaning "
                        f"to its reporting read contract. See {DOC}"
                    )
            for target in write_targets(sql):
                normalized = target
                schema, _table = normalized.split(".", 1)
                if schema != owned_schema:
                    failures.append(
                        f"{relative}:{line}: reporting-foreign-write: {normalized} is outside "
                        f"owned schema {owned_schema}; call the owning module operation. See {DOC}"
                    )
    print("\n".join(failures) if failures else "Reporting read contracts passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
