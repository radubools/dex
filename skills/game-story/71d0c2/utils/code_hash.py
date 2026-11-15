"""Which hash bucket the code station draws a dict or set key in (Model Workshop's view.js bucketOf / bucketCount)."""
import json


def bucket_of(key, m=8):
    """The bucket (0..m-1) the station draws `key` in: ints (and bools) mod m, strings by h = h*31 + code mod 1000003, others by their JSON."""
    if isinstance(key, bool):
        return (1 if key else 0) % m
    if isinstance(key, float) and key.is_integer():
        key = int(key)
    if isinstance(key, int):
        return key % m
    s = key if isinstance(key, str) else json.dumps(key, separators=(",", ":"))
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) % 1000003
    return h % m


def bucket_count(n):
    """How many buckets the station draws for n keys: 8, doubling while n > 0.66 of them."""
    m = 8
    while n > m * 0.66:
        m *= 2
    return m
