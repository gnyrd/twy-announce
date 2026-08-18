"""Billing cycle must never be inferred from marvy.db amount_paid vs price.

marvy.db carries no reliable annual/monthly signal: purchases.recurring_type is
only monthly / None / multiple, never annual. Classifying a purchase as Annual
because amount_paid exceeds a multiple of its price invents a tier that does not
exist, and it has produced wrong membership counts twice (historical_active_counts
and daily_status_report, both 2026-07). The billing cycle is the split_part column
of the HeyMarvelous Active Subscriptions CSVs; read it through hm_subscriptions_dir()
/ membership_history, never marvy.db.

historical_active_counts.py is the single sanctioned exception: its amount-paid
classification is the documented pre-2026-03-19 reconstruction fallback.
"""

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
EXEMPT = {"historical_active_counts.py"}

# Embedded SQL CASE inference: a string that both mentions the amount_paid/price
# ratio and uses a CASE keyword. Prose docstrings carry the ratio in natural
# language and no CASE keyword, so they are not matched.
_RATIO_TEXT = re.compile(r"amount_paid.{0,80}?price\s*\*|price\s*\*.{0,80}?amount_paid", re.I | re.S)
_SQL_CASE = re.compile(r"\b(case|when|then)\b", re.I)


def _names(node):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def _has_price_multiplier(node):
    for n in ast.walk(node):
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mult):
            if any("price" in name.lower() for name in _names(n)):
                return True
    return False


def find_cycle_inference(text):
    """Return [(lineno, kind)] where cycle is inferred from amount_paid/price."""
    hits = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return hits
    for node in ast.walk(tree):
        # Executable Python: amount_paid compared against a price * factor.
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            names = set().union(*[_names(o) for o in operands]) if operands else set()
            if "amount_paid" in names and any(_has_price_multiplier(o) for o in operands):
                hits.append((node.lineno, "python"))
        # Embedded SQL CASE inference inside a string literal.
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if _RATIO_TEXT.search(s) and _SQL_CASE.search(s):
                hits.append((getattr(node, "lineno", 0), "sql"))
    return hits


def test_production_code_never_infers_cycle_from_amount_paid():
    offenders = {}
    for path in sorted(SRC.glob("*.py")):
        if path.name in EXEMPT:
            continue
        hits = find_cycle_inference(path.read_text(encoding="utf-8"))
        if hits:
            offenders[path.name] = hits
    assert not offenders, (
        "Billing cycle inferred from marvy.db amount_paid/price in: "
        + repr(offenders)
        + ". marvy.db has no annual signal; read split_part from the HM Active "
        "Subscriptions CSVs via hm_subscriptions_dir() / membership_history."
    )


# The guard is worthless if it cannot fire. Prove each arm on synthetic input.

def test_guard_catches_python_inference():
    src = "cycle = 'Annual' if amount_paid > price * 3 else 'Monthly'\n"
    assert find_cycle_inference(src)


def test_guard_catches_python_inference_with_named_multiplier_and_alias():
    src = "flag = row.amount_paid > product_price * ANNUAL_MULTIPLIER\n"
    assert find_cycle_inference(src)


def test_guard_catches_embedded_sql_case_inference():
    src = 'SQL = "SELECT CASE WHEN amount_paid > pr.price * 3 THEN 1 END FROM purchases"\n'
    assert find_cycle_inference(src)


def test_guard_ignores_prose_docstring_warning():
    src = '"""Never infer it: amount_paid > price * 3 invents an annual tier."""\n'
    assert not find_cycle_inference(src)


def test_guard_ignores_benign_amount_paid_filter():
    src = "rows = [r for r in purchases if r.amount_paid > 0]\n"
    assert not find_cycle_inference(src)
