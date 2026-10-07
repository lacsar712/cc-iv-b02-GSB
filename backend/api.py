import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, SETTING_DEFAULTS, connect
from rules import judge, parse_string_code, shadow_check

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
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, voc, isc, ff, verdict, reason, now, now),
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


def need_writer(request: Request):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅扫描员可操作")
    return user


def get_settings(conn) -> dict:
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    stored = {r["key"]: r["value"] for r in rows}
    out = {}
    for key, (default, cast) in SETTING_DEFAULTS.items():
        try:
            out[key] = cast(stored.get(key, default))
        except (TypeError, ValueError):
            out[key] = cast(default)
    return out


def settings_meta(conn) -> dict:
    rows = conn.execute(
        "SELECT key, updated_by, updated_at FROM app_settings"
    ).fetchall()
    return {r["key"]: {"by": r["updated_by"], "at": r["updated_at"]} for r in rows}


def recent_array_rows(conn, code: str, now: datetime, window_sec: int) -> list[dict]:
    """窗长内同阵列已入队（pending/done）的扫描，阴影闸门据此对照。

    阵列归属放在 Python 侧按编号前缀判断，避免 LIKE 里 _ / % 通配配串阵列。
    """
    parsed = parse_string_code(code)
    if parsed is None:
        return []
    array, _, _ = parsed
    prefix = f"{array}-串"
    rows = conn.execute(
        """SELECT string_code, fill_factor, created_at FROM iv_scans
           WHERE status IN ('pending', 'done')
             AND created_at >= %s
           ORDER BY id""",
        (now - timedelta(seconds=window_sec),),
    ).fetchall()
    return [r for r in rows if r["string_code"].startswith(prefix)]


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
                      block_reason, created_by, created_at, processed_at, blocked_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
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
    with connect() as conn:
        with conn.transaction():
            # 同组串交单串行化，保证贴门槛几乎同时到的两笔也能被近窗重复抓住。
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextid(%s))", (code,))
            settings = get_settings(conn)

            status = "pending"
            block_reason = None
            blocked_at = None
            if settings["shadow_gate_enabled"]:
                rows = recent_array_rows(conn, code, now, settings["shadow_window_sec"])
                verdict_check = shadow_check(
                    code=code,
                    fill_factor=ff,
                    recent_rows=rows,
                    now=now,
                    jump=settings["shadow_jump"],
                    window_sec=settings["shadow_window_sec"],
                )
                if not verdict_check["passed"]:
                    status = "blocked"
                    block_reason = verdict_check["reason"]
                    blocked_at = now

            row = conn.execute(
                """INSERT INTO iv_scans
                   (string_code, voc_v, isc_a, fill_factor, status, block_reason,
                    blocked_at, created_by, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict,
                             reason, block_reason, created_by, created_at, processed_at,
                             blocked_at""",
                (code, voc, isc, ff, status, block_reason, blocked_at,
                 user["username"], now),
            ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/blocks")
async def list_blocks(request: Request) -> list:
    """拦住履历：只读账号也可翻。关掉开关后旧履历仍在，只是不再添新账。"""
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, voc_v, isc_a, fill_factor, block_reason,
                      created_by, created_at, blocked_at
               FROM iv_scans
               WHERE status = 'blocked'
               ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@get("/api/settings")
async def read_settings(request: Request) -> dict:
    need_login(request)
    with connect() as conn:
        values = get_settings(conn)
        meta = settings_meta(conn)
    return {
        "shadow_gate_enabled": values["shadow_gate_enabled"],
        "shadow_window_sec": values["shadow_window_sec"],
        "shadow_jump": values["shadow_jump"],
        "updated": {
            key: {"by": m["by"], "at": m["at"].isoformat()}
            for key, m in meta.items()
        },
    }


@post("/api/settings")
async def write_settings(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    updates: dict[str, str] = {}
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            raise HTTPException(status_code=400, detail="开关必须是布尔值")
        updates["shadow_gate_enabled"] = "on" if data["enabled"] else "off"
    if "window_sec" in data:
        try:
            window_sec = int(data["window_sec"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="窗长必须是秒数整数")
        if not 5 <= window_sec <= 600:
            raise HTTPException(status_code=400, detail="窗长只能在 5 到 600 秒之间")
        updates["shadow_window_sec"] = str(window_sec)
    if "jump" in data:
        try:
            jump = float(data["jump"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="跳变阈值必须是数字")
        if not 0.01 <= jump <= 1:
            raise HTTPException(status_code=400, detail="跳变阈值只能在 0.01 到 1 之间")
        updates["shadow_jump"] = f"{jump:.4f}"
    if not updates:
        raise HTTPException(status_code=400, detail="没有要更新的设置项")

    now = datetime.now(timezone.utc)
    with connect() as conn:
        for key, value in updates.items():
            conn.execute(
                """INSERT INTO app_settings (key, value, updated_by, updated_at)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (key) DO UPDATE
                     SET value = EXCLUDED.value,
                         updated_by = EXCLUDED.updated_by,
                         updated_at = EXCLUDED.updated_at""",
                (key, value, user["username"], now),
            )
        conn.commit()
        values = get_settings(conn)
        meta = settings_meta(conn)
    return {
        "shadow_gate_enabled": values["shadow_gate_enabled"],
        "shadow_window_sec": values["shadow_window_sec"],
        "shadow_jump": values["shadow_jump"],
        "updated": {
            key: {"by": m["by"], "at": m["at"].isoformat()}
            for key, m in meta.items()
        },
    }


@post("/api/shadow/preview")
async def shadow_preview(request: Request) -> dict:
    """专页上的开关对照试算：按当下窗长/阈值模拟一笔，不写库。"""
    need_login(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="填充因子必须是数字")

    now = datetime.now(timezone.utc)
    with connect() as conn:
        settings = get_settings(conn)
        rows = recent_array_rows(conn, code, now, settings["shadow_window_sec"])
    check = shadow_check(
        code=code,
        fill_factor=ff,
        recent_rows=rows,
        now=now,
        jump=settings["shadow_jump"],
        window_sec=settings["shadow_window_sec"],
    ) if settings["shadow_gate_enabled"] else None
    return {
        "gate_enabled": settings["shadow_gate_enabled"],
        "window_sec": settings["shadow_window_sec"],
        "jump": settings["shadow_jump"],
        "would_block": bool(check and not check["passed"]),
        "duplicate": bool(check and check["duplicate"]),
        "reason": check["reason"] if check else None,
        "neighbor_median": check["neighbor_median"] if check else None,
        "neighbor_samples": check["neighbor_samples"] if check else 0,
    }


app = Litestar(route_handlers=[
    health, login, list_logs, create_log, list_blocks,
    read_settings, write_settings, shadow_preview,
])
