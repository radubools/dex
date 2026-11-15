"""How many trace steps a code-station program takes, to keep it under the station's cap (engine code/trace.js MAX_STEPS)."""
import sys

MAX_STEPS = 3000
FILENAME = "<program>"


def trace_steps(source, globals_=None):
    """The line, call and return events of running `source` with `globals_` as its inputs, counted under sys.settrace."""
    n = 0

    def tr(frame, event, arg):
        nonlocal n
        if frame.f_code.co_filename != FILENAME:
            return None
        if event in ("line", "call", "return"):
            n += 1
        return tr

    g = dict(globals_ or {})
    code = compile(source, FILENAME, "exec")
    sys.settrace(tr)
    try:
        exec(code, g)
    finally:
        sys.settrace(None)
    return n
