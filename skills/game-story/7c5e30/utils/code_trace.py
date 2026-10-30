"""Line hits of a code-station program, counted as the station counts them (sys.settrace), for picking code.runTo targets."""
import sys

FILENAME = "<lesson>"


def line_hits(source, globals_=None, watch=()):
    """Run source with globals_ and return each line event as (line, hit, {watched local: value copy}) plus the final globals."""
    events, counts = [], {}
    g = dict(globals_ or {})

    def tracer(frame, event, arg):
        if frame.f_code.co_filename == FILENAME and event == "line":
            line = frame.f_lineno
            counts[line] = counts.get(line, 0) + 1
            loc = {k: _copy(v) for k, v in frame.f_locals.items() if k in watch}
            events.append((line, counts[line], loc))
        return tracer

    old = sys.gettrace()
    sys.settrace(tracer)
    try:
        exec(compile(source, FILENAME, "exec"), g)
    finally:
        sys.settrace(old)
    return events, g


def total_hits(events):
    """The final hit count per line from line_hits' events: {line: hits}."""
    out = {}
    for line, _, _ in events:
        out[line] = out.get(line, 0) + 1
    return dict(sorted(out.items()))


def _copy(v):
    return v.copy() if hasattr(v, "copy") else v
