"""Tokeniser for arithmetic expression strings.

Parses a string of non-negative integers and the operators +, -, *, /
(with optional whitespace) into a list of (operator, value) pairs.
"""

from __future__ import annotations

from typing import List, Tuple


def parse_arithmetic_tokens(expr: str) -> List[Tuple[str, int]]:
    """Tokenise an expression into (operator, value) pairs with the first value carrying None as its operator."""
    tokens: List[Tuple[str, int]] = []
    i = 0
    n = len(expr)
    current_op = None
    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "+-":
            current_op = ch
            i += 1
            continue
        if ch in "*/":
            current_op = ch
            i += 1
            continue
        if ch.isdigit():
            j = i
            while j < n and expr[j].isdigit():
                j += 1
            tokens.append((current_op, int(expr[i:j])))
            current_op = None
            i = j
            continue
        raise ValueError(f"Unexpected character {ch!r} at position {i}")
    return tokens
