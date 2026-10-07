"""填充因子合格判定，以及邻串阴影剔除闸门。

阴影遮挡会让单点填充因子飞掉。闸门打开时，交单笔先和邻串近窗样本对照：

- 本串 FF 相对邻串窗口中位值跳得太猛（超过跳变阈值）→ 整笔退回拦住履历，不进队；
- 同一组串在窗长内已有一笔入队（贴门槛几乎同时到的两笔）→ 后到的一笔拦住，最多留一笔；
- 可对照的邻串基线不足（少于 MIN_NEIGHBORS 串）→ 不猜测，放行。

闸门关掉时这些规则一概不执行，飞点也能进队；关掉之前写下的拦住履历仍然保留可翻。
"""
import re
from datetime import datetime
from statistics import median

FF_MIN = 0.72

# 邻串阴影闸门缺省参数（专页上可调）
DEFAULT_JUMP = 0.20       # 本串 FF 与邻串窗口中位相差超过 0.20 视为飞点
DEFAULT_WINDOW_SEC = 90   # 近窗窗长：只看最近 90 秒内已入队的扫描做对照
MIN_NEIGHBORS = 2         # 邻串基线少于两串时不判定，放行

_STRING_RE = re.compile(r"^(?P<array>.+?)-串(?P<idx>\d+)$")


def judge(fill_factor: float) -> tuple[str, str]:
    if fill_factor >= FF_MIN:
        return "合格", f"填充因子 {fill_factor:.2f} 不低于 {FF_MIN}"
    return "衰减", f"填充因子 {fill_factor:.2f} 低于 {FF_MIN}"


def parse_string_code(code: str) -> tuple[str, int, int] | None:
    """'阵列A-串03' -> ('阵列A', 3, 2)，末项是编号原文宽度（零填充）。

    解析不出（没有“阵列X-串NN”形态）返回 None。
    """
    m = _STRING_RE.match((code or "").strip())
    if not m:
        return None
    raw = m.group("idx")
    return m.group("array"), int(raw), len(raw)


def neighbor_codes(code: str, span: int = 1) -> list[str]:
    """同阵列编号相邻 span 档的组串编号，按编号升序。"""
    parsed = parse_string_code(code)
    if parsed is None:
        return []
    array, idx, width = parsed
    out = []
    for delta in range(-span, span + 1):
        if delta == 0:
            continue
        n = idx + delta
        if n < 0:
            continue
        out.append(f"{array}-串{n:0{width}d}")
    return out


def _within_window(ts: datetime, now: datetime, window_sec: int) -> bool:
    age = (now - ts).total_seconds()
    return 0 <= age <= window_sec


def shadow_check(
    *,
    code: str,
    fill_factor: float,
    recent_rows: list[dict],
    now: datetime,
    jump: float = DEFAULT_JUMP,
    window_sec: int = DEFAULT_WINDOW_SEC,
) -> dict:
    """对照近窗已入队扫描，判定本笔是不是阴影飞点。

    recent_rows 为同阵列、状态已入队（pending/done）的近期扫描，每行含
    string_code / fill_factor / created_at。返回：

        passed        是否放行入队
        reason        拦住原因（放行时为 None）
        duplicate     是否撞近窗重复
        neighbor_median, neighbor_samples  邻串窗口中位与参与串数
    """
    result = {
        "passed": True,
        "reason": None,
        "duplicate": False,
        "neighbor_median": None,
        "neighbor_samples": 0,
    }
    parsed = parse_string_code(code)
    if parsed is None:
        # 编号解析不出阵列/序号，无法找邻串，不猜测。
        return result

    neighbors = set(neighbor_codes(code))

    # 近窗重复：同组串窗长内已经有一笔入队，后到的拦住。
    for row in recent_rows:
        if row["string_code"] == code and _within_window(row["created_at"], now, window_sec):
            result.update(duplicate=True, passed=False)
            result["reason"] = (
                f"同组串 {code} 在窗长 {window_sec} 秒内已有一笔入队，"
                "贴门槛同时到的两笔最多留一笔"
            )
            return result

    # 邻串基线：窗长内每个相邻组串只取最新一笔，再取中位。
    latest: dict[str, dict] = {}
    for row in recent_rows:
        if row["string_code"] not in neighbors:
            continue
        if not _within_window(row["created_at"], now, window_sec):
            continue
        old = latest.get(row["string_code"])
        if old is None or row["created_at"] > old["created_at"]:
            latest[row["string_code"]] = row

    result["neighbor_samples"] = len(latest)
    if len(latest) < MIN_NEIGHBORS:
        # 基线不足，不判定，放行。
        return result

    med = median(float(r["fill_factor"]) for r in latest.values())
    result["neighbor_median"] = med
    diff = abs(float(fill_factor) - med)
    if diff > jump:
        direction = "高" if float(fill_factor) > med else "低"
        result.update(passed=False)
        result["reason"] = (
            f"填充因子 {float(fill_factor):.2f} 相对邻串窗口中位 {med:.2f} "
            f"跳变 {diff:.2f}（{direction}出阈值 {jump:.2f}），疑似邻串阴影遮挡飞点"
        )
    return result
