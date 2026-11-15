"""Test statistics and confidence sets for the llm-math plot board: Student t tails and quadratic-form ellipses as plot.function formulas."""


def t_tail_fn(nu, var="x"):
    """Pr(|T| > var) for Student's t with an even nu, as a plot.function formula (exact finite sum, no special functions)."""
    if nu < 2 or nu % 2:
        raise ValueError("t_tail_fn needs an even nu >= 2")
    terms, c = [], 1.0
    for k in range(nu // 2):
        if k:
            c *= (2 * k - 1) / (2 * k)
        terms.append(f"{c:.12g}*q^{k}" if k else "1")
    body = " + ".join(terms).replace("q", f"({nu}/({nu} + {var}^2))")
    return f"1 - ({var}/sqrt({nu} + {var}^2))*({body})"


def ellipse_fns(h, k, M, c, tail=0.025):
    """(top, bottom, half x-width) of the ellipse (p - (h, k))^T M (p - (h, k)) = c as plot.function formulas in x; `tail` keeps both halves defined a little past each tip so they join."""
    a, m, d = M[0][0], M[0][1], M[1][1]
    u = f"(x - {h:.5g})"
    disc = f"({m * m - a * d:.6g} {u}^2 + {c * d:.6g})"
    xr = (c * d / (a * d - m * m)) ** 0.5
    mask = f"0 sqrt({xr + tail:.5g} - |{u}|)"
    top = f"{k:.5g} + ({-m:.6g} {u} + sqrt(max({disc}, 0)))/{d:.6g} + {mask}"
    bot = f"{k:.5g} + ({-m:.6g} {u} - sqrt(max({disc}, 0)))/{d:.6g} + {mask}"
    return top, bot, xr
