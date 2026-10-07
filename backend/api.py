import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post, put
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect
from rules import DUP_FF_TOL, DUP_WINDOW_SEC, judge, median, parse_string

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            for code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                parsed = parse_string(code)
                array_code, seq_no = parsed if parsed else (None, None)
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, array_code, seq_no, voc_v, isc_a, fill_factor,
                        status, verdict, reason, created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, array_code, seq_no, voc, isc, ff, verdict, reason, now, now),
                )
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request, detail: str = "仅扫描员可操作"):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail=detail)
    return user


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                      created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


def _record_rejection(conn, *, kind, code, voc, isc, ff, reason,
                      settings, username, now, ref_scan_id=None,
                      neighbor_count=0, neighbor_median=None, settings_enabled=None):
    conn.execute(
        """INSERT INTO scan_rejections
           (kind, string_code, voc_v, isc_a, fill_factor, reason, ref_scan_id,
            neighbor_count, neighbor_median, settings_enabled, threshold, window_minutes,
            rejected_by, rejected_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (kind, code, voc, isc, ff, reason, ref_scan_id, neighbor_count, neighbor_median,
         settings_enabled if settings_enabled is not None else settings["enabled"],
         settings["threshold"], settings["window_minutes"], username, now),
    )


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request, "仅扫描员可提交IV扫描")
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")

    now = datetime.now(timezone.utc)
    parsed = parse_string(code)
    array_code, seq_no = parsed if parsed else (None, None)

    # 拒绝先落履历再以非 2xx 退回：提交口的灯不会亮，飞点也不进队
    rejection = None
    with connect() as conn:
        # 锁设置单行，把并发交单串行化，保证“几乎同时”的判定确定
        settings = conn.execute(
            "SELECT enabled, threshold, window_minutes FROM shadow_settings WHERE id=1 FOR UPDATE"
        ).fetchone()

        dup = conn.execute(
            """SELECT id, fill_factor FROM iv_scans
               WHERE string_code = %s
                 AND created_at >= %s
                 AND abs(fill_factor - %s) <= %s
               ORDER BY id DESC LIMIT 1""",
            (code, now - timedelta(seconds=DUP_WINDOW_SEC), ff, DUP_FF_TOL),
        ).fetchone()
        if dup is not None:
            reason = (f"近时重复：{DUP_WINDOW_SEC}秒内同串已有填充因子近似的扫描单"
                      f"（单号 {dup['id']}，FF {dup['fill_factor']}），最多收一笔")
            _record_rejection(
                conn, kind="duplicate", code=code,
                voc=voc, isc=isc, ff=ff, reason=reason, settings=settings,
                username=user["username"], now=now, ref_scan_id=dup["id"],
            )
            conn.commit()
            rejection = (409, reason)
        elif settings["enabled"] and parsed is not None:
            since = now - timedelta(minutes=settings["window_minutes"])
            neighbors = conn.execute(
                """SELECT fill_factor FROM iv_scans
                   WHERE array_code = %s AND seq_no IS DISTINCT FROM %s
                     AND created_at >= %s""",
                (array_code, seq_no, since),
            ).fetchall()
            med = median([float(r["fill_factor"]) for r in neighbors])
            if med is not None and abs(ff - med) > settings["threshold"]:
                reason = (f"邻串阴影剔除：本串填充因子 {ff:.3f} 相对同阵列邻串近"
                          f"{settings['window_minutes']}分钟中位数 {med:.3f} "
                          f"跳幅 {abs(ff - med):.3f}，超过门槛 {settings['threshold']:.2f}")
                _record_rejection(
                    conn, kind="shadow", code=code,
                    voc=voc, isc=isc, ff=ff, reason=reason, settings=settings,
                    username=user["username"], now=now,
                    neighbor_count=len(neighbors), neighbor_median=med,
                    settings_enabled=True,
                )
                conn.commit()
                rejection = (422, reason)

        if rejection is None:
            row = conn.execute(
                """INSERT INTO iv_scans
                   (string_code, array_code, seq_no, voc_v, isc_a, fill_factor,
                    status, created_by, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s)
                   RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict,
                             reason, created_by, created_at, processed_at""",
                (code, array_code, seq_no, voc, isc, ff, user["username"], now),
            ).fetchone()
            conn.commit()

    if rejection is not None:
        status, detail = rejection
        raise HTTPException(status_code=status, detail=detail)
    return dump(row)


@get("/api/shadow/settings")
async def get_shadow_settings(request: Request) -> dict:
    need_login(request)
    with connect() as conn:
        row = conn.execute(
            """SELECT enabled, threshold, window_minutes, updated_by, updated_at
               FROM shadow_settings WHERE id=1"""
        ).fetchone()
        return dump(row)


@put("/api/shadow/settings")
async def update_shadow_settings(request: Request) -> dict:
    user = need_writer(request, "旁观账号只能查看，不能扳动阴影剔除开关")
    data = await request.json()
    fields, params = [], []
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            raise HTTPException(status_code=400, detail="开关必须是布尔值")
        fields.append("enabled = %s")
        params.append(data["enabled"])
    if "threshold" in data:
        try:
            threshold = float(data["threshold"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="门槛必须是数字")
        if not 0.0 < threshold <= 1.0:
            raise HTTPException(status_code=400, detail="门槛需落在 0 到 1 之间（不含 0）")
        fields.append("threshold = %s")
        params.append(threshold)
    if "window_minutes" in data:
        try:
            window = int(data["window_minutes"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="窗长必须是整数分钟")
        if not 1 <= window <= 1440:
            raise HTTPException(status_code=400, detail="窗长需在 1 到 1440 分钟之间")
        fields.append("window_minutes = %s")
        params.append(window)
    if not fields:
        raise HTTPException(status_code=400, detail="没有可更新的字段")

    fields.append("updated_by = %s")
    params.append(user["username"])
    fields.append("updated_at = %s")
    params.append(datetime.now(timezone.utc))
    with connect() as conn:
        row = conn.execute(
            f"""UPDATE shadow_settings SET {', '.join(fields)}
                WHERE id=1
                RETURNING enabled, threshold, window_minutes, updated_by, updated_at""",
            params,
        ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/shadow/rejections")
async def list_rejections(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, kind, string_code, voc_v, isc_a, fill_factor, reason,
                      ref_scan_id, neighbor_count, neighbor_median, settings_enabled,
                      threshold, window_minutes, rejected_by, rejected_at
               FROM scan_rejections ORDER BY id DESC LIMIT 200"""
        ).fetchall()
        return [dump(r) for r in rows]


app = Litestar(route_handlers=[
    health, login, list_logs, create_log,
    get_shadow_settings, update_shadow_settings, list_rejections,
])
