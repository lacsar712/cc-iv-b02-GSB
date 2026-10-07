"""邻串阴影剔除规则的纯函数单测，不依赖数据库：

    python3 test_rules.py
"""
from datetime import datetime, timedelta, timezone

from rules import (
    DEFAULT_JUMP,
    DEFAULT_WINDOW_SEC,
    neighbor_codes,
    parse_string_code,
    shadow_check,
)

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


def row(code, ff, secs_ago):
    return {
        "string_code": code,
        "fill_factor": ff,
        "created_at": T0 - timedelta(seconds=secs_ago),
    }


def check(code, ff, rows, **kw):
    return shadow_check(
        code=code, fill_factor=ff, recent_rows=rows, now=T0,
        jump=kw.get("jump", DEFAULT_JUMP),
        window_sec=kw.get("window_sec", DEFAULT_WINDOW_SEC),
    )


def test_parse_and_neighbors():
    assert parse_string_code("阵列A-串03") == ("阵列A", 3, 2)
    assert neighbor_codes("阵列B-串11") == ["阵列B-串10", "阵列B-串12"]
    assert neighbor_codes("阵列A-串00") == ["阵列A-串01"]
    assert neighbor_codes("乱码串") == []
    print("ok parse/neighbors")


def test_jump_too_far_is_blocked():
    # 邻串 11/13 中位 0.72，本串 12 飞到 0.99 → 整笔退回
    base = [row("阵列B-串11", 0.71, 10), row("阵列B-串13", 0.73, 20)]
    r = check("阵列B-串12", 0.99, base)
    assert r["passed"] is False and r["duplicate"] is False
    assert r["neighbor_median"] == 0.72 and r["neighbor_samples"] == 2
    assert "阴影" in r["reason"]
    # 往下飞同样拦
    r2 = check("阵列B-串12", 0.40, base)
    assert r2["passed"] is False
    print("ok jump blocked both directions")


def test_within_threshold_passes():
    base = [row("阵列B-串11", 0.71, 10), row("阵列B-串13", 0.73, 20)]
    r = check("阵列B-串12", 0.74, base)
    assert r["passed"] is True and r["reason"] is None
    print("ok within threshold passes")


def test_duplicate_keeps_only_one():
    # 贴门槛几乎同时到：第一笔无同串历史，放行
    base = [row("阵列B-串11", 0.71, 10), row("阵列B-串13", 0.73, 10)]
    first = check("阵列B-串12", 0.72, base)
    assert first["passed"] is True
    # 同串窗长内再来一笔（哪怕数值正常）→ 拦住，最多留一笔
    base.append(row("阵列B-串12", 0.72, 2))
    second = check("阵列B-串12", 0.72, base)
    assert second["passed"] is False and second["duplicate"] is True
    assert "最多留一笔" in second["reason"]
    print("ok near-window duplicate keeps one")


def test_duplicate_outside_window_passes():
    base = [
        row("阵列B-串11", 0.71, 10),
        row("阵列B-串13", 0.73, 10),
        row("阵列B-串12", 0.72, 95),  # 超出 90 秒窗长
    ]
    r = check("阵列B-串12", 0.72, base)
    assert r["passed"] is True
    print("ok stale duplicate ignored")


def test_insufficient_neighbors_passes():
    # 只有一个邻串基线 → 不猜测，飞点也放行（交单口缺基线场景）
    r = check("阵列B-串12", 0.99, [row("阵列B-串11", 0.71, 5)])
    assert r["passed"] is True and r["neighbor_samples"] == 1
    print("ok insufficient baseline passes")


def test_other_array_not_counted():
    # 编号相近但不同阵列，不算邻串
    rows = [row("阵列A-串11", 0.71, 5), row("阵列C-串13", 0.73, 5)]
    r = check("阵列B-串12", 0.99, rows)
    assert r["passed"] is True and r["neighbor_samples"] == 0
    print("ok other array excluded")


def test_unparseable_code_passes():
    r = check("串号丢失", 0.10, [])
    assert r["passed"] is True
    print("ok unparseable code passes")


def test_latest_per_neighbor_median():
    # 每个邻串只取窗内最新一笔：串11 最新 0.80（旧的 0.50 不算）
    rows = [
        row("阵列B-串11", 0.50, 80),
        row("阵列B-串11", 0.80, 5),
        row("阵列B-串13", 0.82, 8),
    ]
    r = check("阵列B-串12", 0.79, rows)
    assert r["neighbor_median"] == 0.81 and r["passed"] is True
    print("ok latest-per-neighbor median")


def main():
    test_parse_and_neighbors()
    test_jump_too_far_is_blocked()
    test_within_threshold_passes()
    test_duplicate_keeps_only_one()
    test_duplicate_outside_window_passes()
    test_insufficient_neighbors_passes()
    test_other_array_not_counted()
    test_unparseable_code_passes()
    test_latest_per_neighbor_median()
    print("ALL RULE TESTS PASSED")


if __name__ == "__main__":
    main()
