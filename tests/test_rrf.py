"""Unit tests for reciprocal rank fusion - pure functions, no database needed."""

from knowledgeos.retrieval import fuse_rankings, reciprocal_rank_fusion


def test_rrf_agrees_when_both_rankings_agree():
    vector = ["a", "b", "c"]
    fts = ["a", "b", "c"]
    scores = reciprocal_rank_fusion([vector, fts], k=60)
    assert scores["a"] > scores["b"] > scores["c"]


def test_rrf_boosts_item_present_in_both_rankings():
    # "b" is #1 in fts but only #3 in vector; "a" is #1 in vector but absent from fts.
    vector = ["a", "c", "b"]
    fts = ["b", "d"]
    scores = reciprocal_rank_fusion([vector, fts], k=60)
    # "b" appears in both lists, "a" only in one -> "b" should outrank "a" even though
    # "a" was ranked #1 somewhere and "b" was not.
    assert scores["b"] > scores["a"]


def test_rrf_ignores_items_missing_from_a_ranking_without_error():
    vector = ["x", "y"]
    fts: list[str] = []
    scores = reciprocal_rank_fusion([vector, fts], k=60)
    assert scores["x"] > scores["y"]
    assert set(scores) == {"x", "y"}


def test_fuse_rankings_respects_limit_and_order():
    vector = ["a", "b", "c", "d"]
    fts = ["b", "a", "d", "c"]
    ordered = fuse_rankings([vector, fts], k=60, limit=2)
    assert ordered == sorted(ordered, key=lambda i: -reciprocal_rank_fusion([vector, fts])[i])
    assert len(ordered) == 2
    # "a" and "b" are top-2 in both source rankings, so they must win fusion.
    assert set(ordered) == {"a", "b"}


def test_rrf_k_parameter_flattens_scores_as_it_grows():
    vector = ["a", "b"]
    scores_small_k = reciprocal_rank_fusion([vector], k=1)
    scores_large_k = reciprocal_rank_fusion([vector], k=1000)
    gap_small_k = scores_small_k["a"] - scores_small_k["b"]
    gap_large_k = scores_large_k["a"] - scores_large_k["b"]
    assert gap_small_k > gap_large_k > 0
