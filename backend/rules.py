import re

FF_MIN = 0.72

# 贴门槛几乎同时到的两笔：同串、FF 近似、时间窗内只收一笔
DUP_WINDOW_SEC = 5
DUP_FF_TOL = 0.005

_STRING_RE = re.compile(r"^(?P<array>.+)-串(?P<seq>\d+)$")


def judge(fill_factor: float) -> tuple[str, str]:
    if fill_factor >= FF_MIN:
        return "合格", f"填充因子 {fill_factor} 不低于 {FF_MIN}"
    return "衰减", f"填充因子 {fill_factor} 低于 {FF_MIN}"


def parse_string(code: str) -> tuple[str, int] | None:
    """把 “阵列A-串03” 拆成 (阵列编号, 串号)；无法识别的编号不参与邻串对照。"""
    m = _STRING_RE.match(code.strip())
    if not m:
        return None
    return m.group("array"), int(m.group("seq"))


def median(values: list[float]) -> float | None:
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2
