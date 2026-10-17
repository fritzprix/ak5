"""Lexorank implementation for conflict-free Kanban ordering.

Format: "0|{rank_string}:"
Using base36 alphabet [0-9a-z] to represent fractional position.
Allows inserting between any two ranks indefinitely with arbitrary precision.
"""

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
BASE = len(ALPHABET)  # 36
CHAR_TO_VAL = {c: i for i, c in enumerate(ALPHABET)}
VAL_TO_CHAR = {i: c for i, c in enumerate(ALPHABET)}

DEFAULT_BUCKET = "0"
INITIAL_MID = "hzzzzz"  # approx midpoint in 6-digit base36 space


def _parse_rank(rank_str: str) -> tuple[str, str]:
    """Parse 'bucket|rank:' into (bucket, rank)."""
    clean = rank_str.strip()
    if "|" in clean:
        bucket, rest = clean.split("|", 1)
        rank_val = rest.rstrip(":")
        return bucket, rank_val
    return DEFAULT_BUCKET, clean.rstrip(":")


def _format_rank(bucket: str, rank_val: str) -> str:
    """Format into standard Lexorank string '0|rank:'."""
    return f"{bucket}|{rank_val}:"


def _string_to_int(s: str, total_len: int) -> int:
    """Convert base36 string to an arbitrary precision integer scaled to total_len digits."""
    val = 0
    for i, ch in enumerate(s):
        val += CHAR_TO_VAL[ch] * (BASE ** (total_len - 1 - i))
    return val


def _int_to_string(val: int, length: int) -> str:
    """Convert integer to base36 string of specified length."""
    chars = []
    current = val
    for _ in range(length):
        chars.append(VAL_TO_CHAR[current % BASE])
        current //= BASE
    res = "".join(reversed(chars))
    # Strip unnecessary trailing '0's but preserve at least one char
    stripped = res.rstrip("0")
    return stripped if stripped else "1"


def initial_rank() -> str:
    """Return default initial rank."""
    return _format_rank(DEFAULT_BUCKET, INITIAL_MID)


def rank_between(prev_rank: str | None = None, next_rank: str | None = None) -> str:
    """Generate a new rank strictly between prev_rank and next_rank.

    - prev_rank is None, next_rank is None -> initial_rank()
    - prev_rank is None, next_rank exists -> rank before next_rank
    - prev_rank exists, next_rank is None -> rank after prev_rank
    - prev_rank and next_rank exist -> rank strictly between them
    """
    if prev_rank is None and next_rank is None:
        return initial_rank()

    bucket = DEFAULT_BUCKET

    if prev_rank is None and next_rank is not None:
        b_next, r_next = _parse_rank(next_rank)
        bucket = b_next
        # Find midpoint between "0" and r_next
        length = max(len(r_next), 6) + 1
        int_next = _string_to_int(r_next, length)
        int_prev = 0
        if int_next <= 1:
            length += 2
            int_next = _string_to_int(r_next, length)
        mid_int = (int_prev + int_next) // 2
        mid_str = _int_to_string(mid_int, length)
        # Ensure strictly less than r_next
        while mid_str >= r_next:
            mid_str = mid_str[:-1] + "0" if len(mid_str) > 1 else "0" + mid_str
        return _format_rank(bucket, mid_str)

    if prev_rank is not None and next_rank is None:
        b_prev, r_prev = _parse_rank(prev_rank)
        bucket = b_prev
        # Find rank strictly greater than r_prev
        length = max(len(r_prev), 6) + 1
        int_prev = _string_to_int(r_prev, length)
        max_int = (BASE ** length) - 1
        if max_int - int_prev <= 1:
            length += 2
            int_prev = _string_to_int(r_prev, length)
            max_int = (BASE ** length) - 1
        mid_int = (int_prev + max_int) // 2
        mid_str = _int_to_string(mid_int, length)
        # Ensure strictly greater than r_prev
        if mid_str <= r_prev:
            mid_str = r_prev + "m"
        return _format_rank(bucket, mid_str)

    # Both are provided
    b_prev, r_prev = _parse_rank(prev_rank)
    b_next, r_next = _parse_rank(next_rank)
    bucket = b_prev

    if r_prev >= r_next:
        raise ValueError(f"prev_rank ({prev_rank}) must be strictly less than next_rank ({next_rank})")

    # Expand precision until there is at least a difference of 2
    length = max(len(r_prev), len(r_next), 6) + 1
    int_prev = _string_to_int(r_prev, length)
    int_next = _string_to_int(r_next, length)

    while int_next - int_prev <= 1:
        length += 2
        int_prev = _string_to_int(r_prev, length)
        int_next = _string_to_int(r_next, length)

    mid_int = (int_prev + int_next) // 2
    mid_str = _int_to_string(mid_int, length)

    # Ensure strictly between
    if not (r_prev < mid_str < r_next):
        # Fallback to appending a middle character to common prefix
        mid_str = r_prev + "i"

    return _format_rank(bucket, mid_str)


def rebalance_ranks(count: int, bucket: str = DEFAULT_BUCKET) -> list[str]:
    """Generate `count` evenly spaced ranks across the base36 space."""
    if count <= 0:
        return []

    length = 6
    max_val = BASE ** length
    step = max_val // (count + 1)

    ranks = []
    for i in range(1, count + 1):
        val = step * i
        rank_str = _int_to_string(val, length)
        ranks.append(_format_rank(bucket, rank_str))
    return ranks
