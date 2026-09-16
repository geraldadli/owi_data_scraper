"""Load notebook definitions without running collections, logins or exports."""
import ast
import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


def load_notebook():
    namespace = dict(globals())
    namespace.update(COLLECTOR_VERSION="test", detect_language=lambda text: None)
    nb = json.loads((Path(__file__).resolve().parents[1] / "buzzer.ipynb").read_text(encoding="utf-8"))
    constants = {
        "CANONICAL_FIELDS", "LIST_FIELDS", "IDENTITY_FIELDS", "ACCOUNT_KEY", "PROFILE_FIELDS",
        "COMMENT_ENDPOINTS", "PROFILE_ENDPOINTS", "COMMENT_PANEL_SELECTORS", "PANEL_FALLBACK",
        "REPLY_BUTTON_PATTERNS", "PARSERS", "ID_COLUMNS", "FEATURE_COLUMNS", "ACCOUNT_FEATURE_COLUMNS",
        "BASELINE_ACCOUNT_FEATURE_COLUMNS", "PROFILE_ACCOUNT_FEATURE_COLUMNS",
        "BASELINE_SCHEMA", "BASELINE_PROPERTIES", "BASELINE_RENAME", "BASELINE_STATUS_VALUES",
        "LEGACY_BASELINE_ACCOUNT_FEATURE_COLUMNS",
        "PROFILE_AUDIT_FIELDS",
    }
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        tree = ast.parse("".join(cell["source"]))
        nodes = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                nodes.append(node)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                # Optional network dependencies are deliberately not imported in offline tests.
                module = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
                if module.split(".")[0] in {"urllib", "hmac", "warnings", "itertools", "sklearn"}:
                    nodes.append(node)
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if names and all(n in constants or n.startswith("RE_") for n in names):
                    nodes.append(node)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "buzzer.ipynb", "exec"), namespace)
    return namespace
