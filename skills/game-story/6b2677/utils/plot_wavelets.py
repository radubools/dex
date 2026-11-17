"""Wavelets for the llm-math plot board: Morlet bursts, their CWT modulus in closed form, Haar pyramids and step signals as plot.function formulas."""
import math

import numpy as np

W0 = 5  # Morlet's centre frequency: psi(u) = e^{i W0 u} e^{-u^2/2}


def _n(v):
    """A number as a plot formula writes it (no exponent notation)."""
    s = f"{v:.6g}"
    return s if "e" not in s else f"{v:.10f}"


def bursts_fn(bursts, w0=W0):
    """A signal of Morlet bursts [(c, s), ...] (centre c, scale s) as a plot.function formula in x."""
    parts = []
    for c, s in bursts:
        u = f"(x - ({_n(c)}))/{_n(s)}"
        parts.append(f"cos({w0}*{u})*exp(-({u})^2/2)")
    return " + ".join(parts)


def bursts_at(x, bursts, w0=W0):
    """The bursts signal of bursts_fn, evaluated with numpy at x."""
    return sum(np.cos(w0 * (x - c) / s) * np.exp(-((x - c) / s) ** 2 / 2) for c, s in bursts)


def morlet_fn(w0=W0):
    """psi_{a,b}(x) = a^{-1/2} cos(w0 (x - b)/a) e^{-((x - b)/a)^2/2} as a formula with parameters a and b."""
    return f"cos({w0}*(x - b)/a)*exp(-((x - b)/a)^2/2)/sqrt(a)"


def cwt_modulus_fn(bursts, w0=W0):
    """|Wf(a, b)| of bursts_fn's signal under the complex Morlet wavelet, as a formula in b with parameter a.

    Gaussian integrals in closed form; the negative-frequency half of each cosine is dropped
    (below e^{-w0^2/2} of the rest).
    """
    re, im = [], []
    for c, s in bursts:
        S2 = f"({_n(s * s)} + a^2)"
        sig = f"({_n(s)}*a/sqrt{S2})"
        m = f"(({_n(c)})*a^2 + b*{_n(s * s)})/{S2}"
        A = f"exp(-(({_n(c)}) - b)^2/(2*{S2}))"
        K = f"({_n(w0 / s)} - {w0}/a)"
        amp = f"0.5*{A}*{sig}*sqrt(2*pi)*exp(-{K}^2*{sig}^2/2)"
        ph = f"({K}*{m} - {_n(w0 / s * c)} + {w0}*b/a)"
        re.append(f"{amp}*cos{ph}")
        im.append(f"{amp}*sin{ph}")
    return f"sqrt(({' + '.join(re)})^2 + ({' + '.join(im)})^2)/sqrt(a)"


def cwt_modulus(a, b, bursts, w0=W0):
    """cwt_modulus_fn's value at (a, b), computed in Python from the same closed form."""
    tot = 0j
    for c, s in bursts:
        S2 = s * s + a * a
        sig = s * a / math.sqrt(S2)
        m = (c * a * a + b * s * s) / S2
        A = math.exp(-((c - b) ** 2) / (2 * S2))
        K = w0 / s - w0 / a
        amp = 0.5 * A * sig * math.sqrt(2 * math.pi) * math.exp(-K * K * sig * sig / 2)
        ph = K * m - w0 / s * c + w0 * b / a
        tot += amp * complex(math.cos(ph), math.sin(ph))
    return abs(tot) / math.sqrt(a)


def cwt_modulus_quad(a, b, bursts, w0=W0, span=30, n=200001):
    """|Wf(a, b)| by trapezoid quadrature on [-span, span]: a check on the closed form."""
    x = np.linspace(-span, span, n)
    u = (x - b) / a
    psi = np.exp(1j * w0 * u) * np.exp(-u * u / 2) / math.sqrt(a)
    return abs(np.trapezoid(bursts_at(x, bursts, w0) * np.conj(psi), x))


R2 = math.sqrt(2)


def haar_step(a):
    """One level of Mallat's fast wavelet transform with Haar filters (1, 1)/sqrt2 and (1, -1)/sqrt2: (approx, detail)."""
    approx = [(a[2 * n] + a[2 * n + 1]) / R2 for n in range(len(a) // 2)]
    detail = [(a[2 * n] - a[2 * n + 1]) / R2 for n in range(len(a) // 2)]
    return approx, detail


def haar_pyramid(x):
    """[(approx, detail)] for each Haar level, finest first, down to one approximation (len(x) a power of 2)."""
    out, a = [], list(x)
    while len(a) > 1:
        a, d = haar_step(a)
        out.append((a, d))
    return out


def block_means(x, level):
    """The level-j blur in the signal's own units: each block of 2^j samples replaced by its mean."""
    w = 2 ** level
    return [sum(x[i - i % w:i - i % w + w]) / w for i in range(len(x))]


def step_fn(values, start=0, steep=1000):
    """A piecewise-constant signal, one value per unit interval from x = start, as a plot.function formula (steep ramps at the jumps)."""
    ramp = lambda t: f"min(max({steep}*(x - {_n(t)}), 0), 1)"
    parts = [_n(values[0])]
    for i in range(1, len(values)):
        jump = values[i] - values[i - 1]
        if jump:
            parts.append(f"{_n(jump)}*{ramp(start + i)}")
    return " + ".join(parts)


def box_fn(a, b, steep=1000, var="x"):
    """1 on [a, b) and 0 elsewhere as a plot.function formula (steep ramps at both ends, step_fn's convention)."""
    ramp = lambda t: f"min(max({steep}*({var} - {_n(t)}), 0), 1)"
    return f"({ramp(a)} - {ramp(b)})"


def haar_coefs(y):
    """Orthonormal Haar coefficients of y (len a power of 2), coarse first: [V0, W0, W1 (2), W2 (4), ...]."""
    a, c = list(map(float, y)), []
    while len(a) > 1:
        a, d = haar_step(a)
        c = d + c
    return a + c


def haar_shrink_fn(y, param="lam", steep=1000):
    """The soft-thresholded Haar fit of y (sample i on [i, i+1)) as a plot.function formula in x with threshold parameter `param`; V0 is not shrunk."""
    n, c = len(y), haar_coefs(y)
    terms = [_n(c[0] / math.sqrt(n))]
    k, size = 1, n
    while size > 1:
        for j in range(n // size):
            coef = c[k]
            k += 1
            if abs(coef) < 1e-12:
                continue
            a, m, b = j * size, j * size + size // 2, (j + 1) * size
            shrunk = f"sign({_n(coef)})*relu({_n(abs(coef))} - {param})"
            terms.append(f"{shrunk}*{_n(1 / math.sqrt(size))}*({box_fn(a, m, steep)} - {box_fn(m, b, steep)})")
        size //= 2
    return " + ".join(terms)


def haar_level_fn(y, level, gap=3.4, scale=0.35, param="lam", steep=1000):
    """Level `level`'s soft-thresholded Haar coefficients of y as bars over their time spans, drawn at height gap * level, scaled by `scale`: one row of ESL Figure 5.17."""
    n, c = len(y), haar_coefs(y)
    size = n // 2 ** level
    terms = [_n(gap * level)]
    for k, coef in enumerate(c[2 ** level:2 ** (level + 1)]):
        if abs(coef) < 1e-12:
            continue
        terms.append(f"{_n(scale)}*sign({_n(coef)})*relu({_n(abs(coef))} - {param})*{box_fn(k * size, (k + 1) * size, steep)}")
    return " + ".join(terms)
