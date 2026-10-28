"""Teach Astroid about fields declared in a controller's sibling DocType JSON.

Only declared fields are injected, so misspelled attributes remain lint errors.
This does not import Frappe, connect to a site, or change runtime classes.
"""

import json
from pathlib import Path

import astroid
from astroid import nodes


def document_fields(node):
    """Expose schema-backed attributes to static analysis."""
    if not any(base.as_string().split(".")[-1] == "Document" for base in node.bases):
        return
    filename = node.root().file
    if not filename:
        return
    schema_path = Path(filename).with_suffix(".json")
    if not schema_path.is_file():
        return
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if schema.get("doctype") != "DocType":
        return
    for field in schema.get("fields", []):
        name = field.get("fieldname")
        if name and name.isidentifier() and name not in node.locals:
            node.locals[name] = [nodes.Unknown(parent=node)]


def register(_linter):
    """Pylint plugin entry point."""
    astroid.MANAGER.register_transform(nodes.ClassDef, document_fields)
