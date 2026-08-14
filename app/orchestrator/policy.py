import re
from pathlib import Path

from app.errors import AppError

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"sk-[A-Za-z0-9]{8,}")),
    ("password_assignment", re.compile(r"password\s*=", re.IGNORECASE)),
    ("eval_call", re.compile(r"\beval\s*\(")),
]


def check_artifacts(directory: Path) -> None:
    """Fail the node if artifact text matches a deny rule. Never echo secrets."""

    if not directory.exists():
        return
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for rule_name, pattern in RULES:
            if pattern.search(text):
                raise AppError(422, "policy_violation", f"Policy rule failed: {rule_name}")
