## `tokens`

Tokeniser for arithmetic expression strings.

- `parse_arithmetic_tokens(expr: str) -> List[Tuple[str, int]]` — Tokenise an expression into (operator, value) pairs with the first value carrying None as its operator.