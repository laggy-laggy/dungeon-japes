#!/usr/bin/env python3
"""Turn Roll20 chat archive exports into an inspectable SQLite database.

Usage (Python 3.9+, standard library only):
    python roll20_parse.py pages/ --output campaign_rolls.sqlite --expect-pages 108
    python roll20_parse.py pages/ --output campaign_rolls.sqlite --csv-dir tables

`pages/` can contain page1.json ... page108.json, or saved .html pages. JSON
exports may contain one or several objects with `page` and `html` fields.
The database preserves source page HTML/metadata, original message JSON, and
each full roll JSON. Re-running replaces the output database. Keep your source
exports as an independent backup.
"""

import argparse
import ast
import base64
import csv
from datetime import datetime, timezone
import hashlib
from html import unescape
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile


MSGDATA_RE = re.compile(r"\b(?:var|let|const)\s+msgdata\s*=\s*['\"]([A-Za-z0-9+/=]+)['\"]")
PAGE_RE = re.compile(r"Page\s+([0-9]+)\s*/\s*([0-9]+)", re.I)
FIELD_RE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*=(.*?)\}\}(?!\})", re.S)
REFERENCE_RE = re.compile(r"\$\[\[([0-9]+)\]\]")
SCHEMA = """
CREATE TABLE source_pages (
  page_record_id INTEGER PRIMARY KEY, page INTEGER, source_file TEXT NOT NULL,
  archive_url TEXT, metadata_json TEXT NOT NULL, html TEXT NOT NULL
);
CREATE TABLE messages (
  message_id TEXT PRIMARY KEY, page_record_id INTEGER REFERENCES source_pages(page_record_id),
  source_order INTEGER, page INTEGER, source_file TEXT,
  time_ms INTEGER, time_utc TEXT, who TEXT, player_id TEXT,
  character_id TEXT, character_name TEXT, message_type TEXT, roll_template TEXT,
  template_fields_json TEXT NOT NULL,
  secrecy TEXT, content TEXT, chat_category TEXT NOT NULL,
  text_words INTEGER, text_characters INTEGER, raw_json TEXT NOT NULL
);
CREATE TABLE rolls (
  message_id TEXT NOT NULL REFERENCES messages(message_id),
  roll_index INTEGER NOT NULL, player_id TEXT, character_id TEXT,
  kind TEXT NOT NULL, role TEXT,
  all_roles_json TEXT NOT NULL, label TEXT, expression TEXT,
  total REAL, modifier_terms_json TEXT NOT NULL, modifier_sum REAL,
  has_dice INTEGER NOT NULL, raw_json TEXT NOT NULL,
  PRIMARY KEY (message_id, roll_index)
);
CREATE TABLE dice (
  die_id INTEGER PRIMARY KEY, message_id TEXT NOT NULL,
  roll_index INTEGER NOT NULL, player_id TEXT, character_id TEXT,
  path TEXT NOT NULL,
  face_index INTEGER NOT NULL, sides INTEGER NOT NULL, face INTEGER NOT NULL,
  dice_in_group INTEGER, result_kind TEXT NOT NULL, table_item_name TEXT,
  is_discarded INTEGER NOT NULL,
  modifiers_json TEXT NOT NULL, result_json TEXT NOT NULL,
  FOREIGN KEY (message_id, roll_index) REFERENCES rolls(message_id, roll_index)
);
CREATE INDEX dice_by_sides ON dice(sides, face);
CREATE INDEX dice_by_roll ON dice(message_id, roll_index);
CREATE INDEX rolls_by_role ON rolls(role);
CREATE INDEX messages_by_actor ON messages(who, player_id);
CREATE VIEW roll_details AS
  SELECT r.*, m.page, m.source_order, m.time_utc, m.who,
         m.character_name, m.roll_template,
         m.template_fields_json, m.message_type
  FROM rolls AS r JOIN messages AS m USING (message_id);
CREATE VIEW die_details AS
  SELECT d.*, r.kind, r.role, r.label, r.expression, r.total,
         m.page, m.source_order, m.time_utc, m.who,
         m.character_name, m.roll_template,
         m.template_fields_json
  FROM dice AS d JOIN rolls AS r USING (message_id, roll_index)
       JOIN messages AS m USING (message_id);
CREATE VIEW numbered_die_details AS
  SELECT * FROM die_details WHERE result_kind='numbered_die';
CREATE VIEW text_chat AS
  SELECT message_id, page, time_utc, who, player_id, secrecy,
         content, text_words, text_characters
  FROM messages WHERE chat_category='plain_text';
"""


def compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def iter_pages(path):
    files = sorted(path.rglob("*")) if path.is_dir() else [path]
    for file in files:
        if not file.is_file() or file.suffix.lower() not in (".json", ".html", ".htm"):
            continue
        try:
            text = file.read_text(encoding="utf-8-sig")
            if file.suffix.lower() == ".json":
                items = json.loads(text)
                if isinstance(items, dict):
                    items = [items]
                if not isinstance(items, list):
                    raise ValueError("expected JSON list or object")
            else:
                items = [{"html": text}]
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("html"), str):
                    raise ValueError("each page must have an html string")
                page = item.get("page")
                if page is None:
                    found = PAGE_RE.search(item["html"])
                    page = int(found.group(1)) if found else None
                match = MSGDATA_RE.search(item["html"])
                if not match:
                    raise ValueError("could not find the embedded msgdata assignment")
                messages = json.loads(base64.b64decode(match.group(1), validate=True))
                if isinstance(messages, list):
                    # Roll20 currently wraps one dictionary in a one-item list.
                    if len(messages) != 1 or not isinstance(messages[0], dict):
                        raise ValueError("unexpected msgdata list structure")
                    messages = messages[0]
                if not isinstance(messages, dict):
                    raise ValueError("msgdata is not a message dictionary")
                yield file, page, messages, item
        except (OSError, ValueError, UnicodeError, KeyError) as exc:
            raise ValueError(f"{file}: {exc}") from exc


def template_fields(content):
    fields = {}
    for key, value in FIELD_RE.findall(content):
        fields.setdefault(key, []).append(value.strip())
    return fields


def label_from_fields(fields):
    label = next(iter(fields.get("rname", []) or fields.get("name", []) or
                      fields.get("charname", [])), None)
    if label:
        # A linked attack name looks like [Radiant Mace](~character|attack).
        match = re.match(r"\[([^]]+)\]\([^)]*\)", label)
        if match:
            label = match.group(1)
    if label and label.startswith("^{") and label.endswith("}"):
        label = label[2:-1].removesuffix("-u").replace("-", " ").title()
    return unescape(label) if label else None


def chat_classification(message):
    """Conservative categorization; original content remains available."""
    content = message.get("content") or ""
    if is_standalone_roll(message) or message.get("inlinerolls"):
        return "roll", None, None
    if message.get("rolltemplate") or "{{" in content:
        return "sheet_card", None, None
    if message.get("type") != "general":
        return "other", None, None
    if re.match(r"^\s*[/!]\S+", content):
        return "command", None, None
    clean = unescape(re.sub(r"<[^>]*>", " ", content)).strip()
    if not clean:
        return "other", None, None
    words = len(re.findall(r"\b\w+(?:['’]\w+)*\b", clean, re.UNICODE))
    return "plain_text", words, len(clean)


def is_standalone_roll(message):
    """Includes public, GM, secret, and hidden rolls with an origRoll."""
    return message.get("origRoll") is not None or message.get("type") in (
        "rollresult", "gmrollresult", "secretrollresult"
    )


def numeric_additive(expression):
    """Evaluate numeric + and - terms only; leave other formulas untouched."""
    try:
        root = ast.parse(str(expression), mode="eval").body
    except SyntaxError:
        return None

    def number(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = number(node.operand)
            return (value if isinstance(node.op, ast.UAdd) else -value) if value is not None else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            left, right = number(node.left), number(node.right)
            if left is not None and right is not None:
                return left + right if isinstance(node.op, ast.Add) else left - right
        return None

    return number(root)


def dice_nodes(obj, path="$", group_discarded=False):
    """Yield dice and carry drop flags from enclosing keep/drop groups."""
    if isinstance(obj, dict):
        if obj.get("type") == "R":
            yield path, obj, group_discarded
            return
        for key, val in obj.items():
            if obj.get("type") == "G" and key == "rolls" and isinstance(val, list):
                outcomes = obj.get("results") or []
                for index, branch in enumerate(val):
                    dropped = (index < len(outcomes) and
                               isinstance(outcomes[index], dict) and
                               bool(outcomes[index].get("d", False)))
                    yield from dice_nodes(branch, f"{path}.rolls[{index}]",
                                          group_discarded or dropped)
            else:
                yield from dice_nodes(val, f"{path}.{key}", group_discarded)
    elif isinstance(obj, list):
        for index, val in enumerate(obj):
            yield from dice_nodes(val, f"{path}[{index}]", group_discarded)


def modifier_terms(obj):
    """Preserve Roll20 math terms and their optional following labels."""
    terms = []

    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "M":
                expression = value.get("expr")
                numeric = numeric_additive(expression)
                terms.append({"expression": expression, "label": None, "value": numeric})
            elif value.get("type") == "L" and terms and terms[-1]["label"] is None:
                terms[-1]["label"] = value.get("text")
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(obj)
    return terms


def add_roll(db, mid, player_id, character_id, index, kind, roll, expression, fields, label):
    roles = [key for key, values in fields.items()
             if any(int(n) == index for value in values
                    for n in REFERENCE_RE.findall(value))] if kind == "inline" else []
    nodes = list(dice_nodes(roll))
    has_dice = any(node.get("results") for _, node, _ in nodes)
    total = roll.get("total") if isinstance(roll, dict) else None
    terms = modifier_terms(roll)
    modifier_sum = (sum(term["value"] for term in terms)
                    if terms and all(term["value"] is not None for term in terms) else None)
    db.execute("INSERT INTO rolls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (mid, index, player_id, character_id, kind,
                roles[0] if roles else None,
                compact(roles), label, expression, total, compact(terms),
                modifier_sum, int(has_dice), compact(roll)))
    for path, node, group_discarded in nodes:
        for face_index, result in enumerate(node.get("results", [])):
            if not isinstance(result, dict) or not isinstance(result.get("v"), (int, float)):
                raise ValueError(f"Unexpected die result in message {mid}: {result!r}")
            if "tableItem" in result:
                result_kind = "roll_table"
                table_item = result["tableItem"]
                table_name = table_item.get("name") if isinstance(table_item, dict) else None
            elif node["sides"] == 3 and re.search(r"\b\d*dF\b", expression or "", re.I):
                result_kind, table_name = "fate_die", None
            else:
                result_kind, table_name = "numbered_die", None
            db.execute("""INSERT INTO dice
              (message_id,roll_index,player_id,character_id,path,face_index,sides,face,dice_in_group,
               result_kind,table_item_name,is_discarded,modifiers_json,result_json)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (mid, index, player_id, character_id, path, face_index,
               node["sides"], result["v"], node.get("dice"),
               result_kind, table_name,
               int(bool(result.get("d", False)) or group_discarded),
               compact(node.get("mods", {})), compact(result)))


def parse(path, output, expect_pages=None):
    # Build separately so an incomplete export cannot destroy a previous DB.
    fd, temporary_name = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp",
                                          dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    seen_pages = 0
    page_numbers = set()
    duplicates = 0
    db = None
    try:
        db = sqlite3.connect(temporary)
        db.executescript(SCHEMA)
        with db:
          for file, page, messages, page_item in iter_pages(path):
            seen_pages += 1
            if page is not None:
                page_numbers.add(int(page))
            page_record_id = db.execute("""INSERT INTO source_pages
              (page,source_file,archive_url,metadata_json,html) VALUES (?,?,?,?,?)""",
              (page, str(file), page_item.get("url"),
               compact({key: value for key, value in page_item.items() if key != "html"}),
               page_item["html"])).lastrowid
            for source_order, (mid, message) in enumerate(messages.items()):
                if not isinstance(message, dict):
                    raise ValueError(f"{file}: message {mid} is not an object")
                # Stable fallback if a future export omits the dictionary key.
                mid = mid or hashlib.sha256(compact(message).encode()).hexdigest()
                priority = message.get(".priority") or message.get("timestamp")
                time_ms = int(priority) if priority is not None else None
                utc = (datetime.fromtimestamp(time_ms / 1000, timezone.utc).isoformat()
                       if time_ms is not None else None)
                category, words, characters = chat_classification(message)
                fields = template_fields(message.get("content", ""))
                character_name = next(iter(fields.get("charname", [])), None)
                inserted = db.execute("""INSERT OR IGNORE INTO messages VALUES
                  (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (mid, page_record_id, source_order, page, str(file),
                   time_ms, utc, message.get("who"),
                   message.get("playerid"), message.get("rolledByCharacterId"),
                   character_name, message.get("type"), message.get("rolltemplate"),
                   compact(fields),
                   message.get("secrecy"), message.get("content"), category,
                   words, characters, compact(message)))
                if not inserted.rowcount:
                    duplicates += 1
                    continue
                label = label_from_fields(fields)
                if is_standalone_roll(message):
                    try:
                        roll = json.loads(message["content"])
                    except (ValueError, KeyError) as exc:
                        raise ValueError(f"{file}: invalid standalone roll {mid}: {exc}") from exc
                    if not isinstance(roll, dict) or not isinstance(roll.get("rolls"), list):
                        raise ValueError(f"{file}: unsupported standalone roll structure {mid}")
                    add_roll(db, mid, message.get("playerid"),
                             message.get("rolledByCharacterId"), -1, "standalone", roll,
                             message.get("origRoll"), {}, None)
                for index, item in enumerate(message.get("inlinerolls") or []):
                    add_roll(db, mid, message.get("playerid"),
                             message.get("rolledByCharacterId"), index,
                             "inline", item.get("results") or {},
                             item.get("expression"), fields, label)
        if seen_pages == 0:
            raise ValueError("no exported chat pages found")
        if expect_pages is not None:
            expected = set(range(1, expect_pages + 1))
            missing = sorted(expected - page_numbers)
            extra = sorted(page_numbers - expected)
            if missing or extra or seen_pages != expect_pages:
                raise ValueError(f"expected pages 1..{expect_pages}; "
                                 f"read {seen_pages} pages, missing {missing}, extra {extra}")
        counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("source_pages", "messages", "rolls", "dice")}
        counts["rolled_expressions"] = db.execute(
            "SELECT COUNT(*) FROM rolls WHERE has_dice=1").fetchone()[0]
        counts["rolls_without_player_id"] = db.execute(
            "SELECT COUNT(*) FROM rolls WHERE has_dice=1 AND player_id IS NULL").fetchone()[0]
        counts["pages"] = seen_pages
        counts["duplicates_skipped"] = duplicates
        db.close()
        db = None
        os.replace(temporary, output)
        return counts
    finally:
        if db is not None:
            db.close()
        temporary.unlink(missing_ok=True)


def export_csv(database, directory):
    directory.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        for table in ("source_pages", "messages", "rolls", "dice"):
            cursor = db.execute(f"SELECT * FROM {table}")
            with (directory / f"{table}.csv").open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([column[0] for column in cursor.description])
                writer.writerows(cursor)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="folder of exported pages or one export file")
    parser.add_argument("--output", type=Path, default=Path("campaign_rolls.sqlite"))
    parser.add_argument("--csv-dir", type=Path,
                        help="also export source_pages, messages, rolls, dice CSV files")
    parser.add_argument("--expect-pages", type=int, help="require exactly pages 1 through N")
    args = parser.parse_args()
    if not args.input.exists():
        parser.error(f"input does not exist: {args.input}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        counts = parse(args.input, args.output, args.expect_pages)
        if args.csv_dir:
            export_csv(args.output, args.csv_dir)
    except (ValueError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print("Parsed:", ", ".join(f"{key}={value}" for key, value in counts.items()))
    print("Database:", args.output.resolve())
    if args.csv_dir:
        print("CSV folder:", args.csv_dir.resolve())


if __name__ == "__main__":
    main()
