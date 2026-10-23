"""Numbers formatted exactly as Model Workshop's bench prints them, so story text matches the live numbers."""

from decimal import ROUND_HALF_UP, Decimal


def fmt(v, digits=2):
    """`v` as the game's fmt (actions/general.js): integers as they are, others `digits` decimals, a true minus sign (U+2212)."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int) or float(v).is_integer():
        s = str(int(v))
    else:
        # JS toFixed rounds the exact binary value half away from zero; Decimal(v) is that exact value.
        q = Decimal(v).copy_abs().quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
        s = ("-" if v < 0 else "") + f"{q:.{digits}f}"
    if s.startswith("-"):
        s = "−" + s[1:]
    if s.startswith("−") and set(s[1:]) <= set("0."):
        s = s[1:]
    return s
