from ak5.services.lexorank import initial_rank, rank_between, rebalance_ranks


def test_initial_rank():
    r = initial_rank()
    assert r.startswith("0|")
    assert r.endswith(":")


def test_rank_between_empty():
    r = rank_between(None, None)
    assert r == initial_rank()


def test_rank_between_insert_first():
    mid = initial_rank()
    first = rank_between(None, mid)
    assert first < mid


def test_rank_between_insert_last():
    mid = initial_rank()
    last = rank_between(mid, None)
    assert mid < last


def test_rank_between_insert_middle():
    r1 = "0|100000:"
    r2 = "0|300000:"
    mid = rank_between(r1, r2)
    assert r1 < mid < r2


def test_rank_between_tight_space():
    r1 = "0|100000:"
    r2 = "0|100001:"
    mid = rank_between(r1, r2)
    assert r1 < mid < r2


def test_rank_between_successive_inserts():
    ranks = [initial_rank()]
    # Insert 10 items before first item
    for _ in range(10):
        new_rank = rank_between(None, ranks[0])
        assert new_rank < ranks[0]
        ranks.insert(0, new_rank)

    # Verify all are strictly sorted
    assert ranks == sorted(ranks)


def test_rebalance_ranks():
    count = 5
    ranks = rebalance_ranks(count)
    assert len(ranks) == count
    assert ranks == sorted(ranks)
