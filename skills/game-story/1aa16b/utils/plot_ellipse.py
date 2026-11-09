"""Ellipses for the llm-math plot board: a matrix's image of the unit circle (semi-axes a, b at angle t) and its diameters, as plot.function formulas."""

# The board samples y = f(x) at 64 evenly spaced x and draws nothing where f is
# not finite. Each half below is clamped at its tip and defined MARGIN past it,
# so the top and bottom halves meet one sample beyond the true tip instead of
# leaving a gap. MARGIN suits plot_balls' window (BALL_X: samples 0.1 apart).
MARGIN = 0.105

_C, _S = "cos(pi*t/180)", "sin(pi*t/180)"
_P = f"({_S}^2/a^2 + {_C}^2/b^2)"
_X_END = f"sqrt(a^2*{_C}^2 + b^2*{_S}^2)"
_CENTRE = f"x*{_S}*{_C}*(1/b^2 - 1/a^2)"
_ROOT = f"sqrt(max({_P} - x^2/(a^2*b^2), 0))"
_DOM = f"0*sqrt({_X_END} + {MARGIN} - abs(x))"

# The ellipse with semi-axis a along angle t (degrees, from x toward y) and b
# across it: (x c + y s)^2/a^2 + (-x s + y c)^2/b^2 = 1 solved for y. With
# a = sigma_1, b = sigma_2 and t the angle of u_1 it is A's image of the unit circle.
ELLIPSE_TOP = f"({_CENTRE} + {_ROOT})/{_P} + {_DOM}"
ELLIPSE_BOTTOM = f"({_CENTRE} - {_ROOT})/{_P} + {_DOM}"

# Its two diameters: length 2a along t, and length 2b along t + 90 (not drawn
# when vertical, t = 0: y = f(x) cannot draw an upright segment).
_K1, _K2 = f"(a*abs({_C}))", f"(b*abs({_S}))"
DIAMETER_A = f"tan(pi*t/180)*max(-{_K1}, min({_K1}, x)) + 0*sqrt({_K1} + {MARGIN} - abs(x))"
DIAMETER_B = f"-max(-{_K2}, min({_K2}, x))/tan(pi*t/180) + 0*sqrt({_K2} + {MARGIN} - abs(x))"

# The unit circle, closed exactly at x = +-1 (board samples land there in plot_balls' window).
CIRCLE_TOP = "sqrt(max(1 - x^2, 0)) + 0*sqrt(1.01 - abs(x))"
CIRCLE_BOTTOM = "-sqrt(max(1 - x^2, 0)) - 0*sqrt(1.01 - abs(x))"
