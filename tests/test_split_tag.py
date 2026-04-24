#!/usr/bin/env python3
"""Tests for </分段> tag-based message splitting."""

from qq_agent_mcp.tools import _decide_chunks, _split_by_tag


def test_no_tag_returns_none():
    assert _split_by_tag("普通消息没有标签") is None


def test_single_tag_splits_into_two():
    assert _split_by_tag("吃了吗</分段>今天忙不忙") == ["吃了吗", "今天忙不忙"]


def test_multiple_tags_split_into_n():
    assert _split_by_tag("一</分段>二</分段>三") == ["一", "二", "三"]


def test_consecutive_tags_drop_empty():
    assert _split_by_tag("前</分段></分段>后") == ["前", "后"]


def test_leading_tag_dropped():
    assert _split_by_tag("</分段>内容") == ["内容"]


def test_trailing_tag_dropped():
    assert _split_by_tag("内容</分段>") == ["内容"]


def test_whitespace_around_tag_stripped():
    assert _split_by_tag("前面  </分段>  后面") == ["前面", "后面"]


def test_newlines_around_tag_stripped():
    assert _split_by_tag("前面\n</分段>\n后面") == ["前面", "后面"]


def test_tag_with_inner_whitespace_tolerated():
    assert _split_by_tag("前</ 分段 >后") == ["前", "后"]


def test_only_tag_returns_empty_list():
    assert _split_by_tag("</分段>") == []


def test_empty_string_returns_none():
    assert _split_by_tag("") is None


# ── _decide_chunks: end-to-end decision logic ──────────────


def test_decide_no_split_plain_text():
    assert _decide_chunks("你好", split_content=False, num_chunks=None) == ["你好"]


def test_decide_strips_outer_whitespace():
    assert _decide_chunks("  hi  ", split_content=False, num_chunks=None) == ["hi"]


def test_decide_empty_returns_empty_list():
    assert _decide_chunks("   ", split_content=False, num_chunks=None) == []


def test_decide_tag_splits_by_default():
    """Tag triggers split even without split_content/num_chunks."""
    assert _decide_chunks(
        "吃了吗</分段>今天忙不忙", split_content=False, num_chunks=None
    ) == ["吃了吗", "今天忙不忙"]


def test_decide_num_chunks_1_overrides_tag():
    """num_chunks=1 forces single message even when tag present."""
    assert _decide_chunks(
        "吃了吗</分段>今天忙不忙", split_content=False, num_chunks=1
    ) == ["吃了吗</分段>今天忙不忙"]


def test_decide_num_chunks_overrides_tag():
    """num_chunks>=2 uses its own logic; tag in content is not used as a split point."""
    # num_chunks>=2 routes through _chunk_message which doesn't know about
    # the tag. The tag survives as literal text — it is NOT treated as a splitter.
    result = _decide_chunks(
        "吃了吗</分段>今天忙不忙", split_content=False, num_chunks=2
    )
    # Critical: result must NOT be the tag-split result
    assert result != ["吃了吗", "今天忙不忙"]
    # Tag survives as literal text
    assert "</分段>" in "".join(result)


def test_decide_tag_chunks_not_further_split_by_punctuation():
    """Each tag-delimited segment is sent as-is, not re-chunked by punctuation."""
    # Second segment has commas that _chunk_message would split — must stay whole.
    assert _decide_chunks(
        "你好</分段>今天,天气,真的,非常,好,我们,去,公园,玩,好不好",
        split_content=False,
        num_chunks=None,
    ) == ["你好", "今天,天气,真的,非常,好,我们,去,公园,玩,好不好"]


def test_decide_split_content_still_works_without_tag():
    """Existing short-message punctuation split still fires when no tag."""
    result = _decide_chunks("你好。你呢？", split_content=True, num_chunks=None)
    assert len(result) >= 1  # _chunk_message decides the exact count


def test_decide_tag_wins_over_split_content():
    """When both tag and split_content are present, tag wins."""
    assert _decide_chunks(
        "一段</分段>另一段", split_content=True, num_chunks=None
    ) == ["一段", "另一段"]


if __name__ == "__main__":
    tests = [
        test_no_tag_returns_none,
        test_single_tag_splits_into_two,
        test_multiple_tags_split_into_n,
        test_consecutive_tags_drop_empty,
        test_leading_tag_dropped,
        test_trailing_tag_dropped,
        test_whitespace_around_tag_stripped,
        test_newlines_around_tag_stripped,
        test_tag_with_inner_whitespace_tolerated,
        test_only_tag_returns_empty_list,
        test_empty_string_returns_none,
        test_decide_no_split_plain_text,
        test_decide_strips_outer_whitespace,
        test_decide_empty_returns_empty_list,
        test_decide_tag_splits_by_default,
        test_decide_num_chunks_1_overrides_tag,
        test_decide_num_chunks_overrides_tag,
        test_decide_tag_chunks_not_further_split_by_punctuation,
        test_decide_split_content_still_works_without_tag,
        test_decide_tag_wins_over_split_content,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    exit(1 if failed else 0)
