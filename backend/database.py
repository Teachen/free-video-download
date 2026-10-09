"""数据访问层。

支持双后端，按环境自动切换：
    - 本地开发：SQLite（backend/data/app.db）
    - 微信云托管：内置 MySQL（平台自动注入 MYSQL_ADDRESS / MYSQL_USERNAME / MYSQL_PASSWORD）

之所以要 MySQL：微信云托管容器每次重新部署文件系统都会重置，
SQLite 文件无法持久化（微信云托管不支持 CFS 挂载），账号/订单会丢失。

对上层屏蔽差异：所有查询函数都用 ``?`` 占位符，由 _MysqlConn 自动转换为 ``%s``。
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# 环境探测
# --------------------------------------------------------------------------- #
MYSQL_ADDRESS = os.getenv("MYSQL_ADDRESS", "").strip()
MYSQL_USERNAME = os.getenv("MYSQL_USERNAME", "root").strip()
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "").strip()
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "saveany").strip()

USE_MYSQL = bool(MYSQL_ADDRESS)

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "app.db")


def _parse_mysql_address(addr: str) -> tuple[str, int]:
    """MYSQL_ADDRESS 形如 ``10.0.0.12:3306`` 或 ``10.0.0.12``"""
    if ":" in addr:
        host, _, port = addr.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            return addr, 3306
    return addr, 3306


# --------------------------------------------------------------------------- #
# SQLite 分支
# --------------------------------------------------------------------------- #
def get_db_path():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return DB_PATH


# --------------------------------------------------------------------------- #
# MySQL 适配层：把 sqlite 风格的调用转成 pymysql
# --------------------------------------------------------------------------- #
def _translate_sql(sql: str) -> str:
    """把 sqlite 方言转成 MySQL 方言"""
    sql = sql.replace("?", "%s")
    sql = sql.replace("datetime('now')", "NOW()")
    sql = sql.replace("datetime(\"now\")", "NOW()")
    return sql


class _MysqlConn:
    """包装 pymysql 连接，对外暴露与 sqlite3.Connection 一致的 execute/executescript"""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params=()):
        cursor = self._conn.cursor()
        cursor.execute(_translate_sql(sql), params if params else None)
        return cursor

    def executescript(self, script: str):
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                cursor = self._conn.cursor()
                cursor.execute(_translate_sql(stmt))

    def __getattr__(self, item):
        return getattr(self._conn, item)


def _connect_mysql():
    """建立 MySQL 连接，带一次重试（云托管 MySQL 会自动暂停后唤醒）"""
    import pymysql

    host, port = _parse_mysql_address(MYSQL_ADDRESS)
    kwargs = dict(
        host=host,
        port=port,
        user=MYSQL_USERNAME,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
    )
    last_err = None
    for _ in range(2):
        try:
            return pymysql.connect(**kwargs)
        except Exception as e:  # 自动暂停后首次连接可能失败，重试一次
            last_err = e
    raise last_err


def _ensure_mysql_database():
    """数据库不存在时自动创建（云托管 MySQL 首次部署常见）"""
    import pymysql

    host, port = _parse_mysql_address(MYSQL_ADDRESS)
    conn = pymysql.connect(
        host=host, port=port, user=MYSQL_USERNAME, password=MYSQL_PASSWORD,
        charset="utf8mb4", connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_db():
    if USE_MYSQL:
        raw = _connect_mysql()
        conn = _MysqlConn(raw)
        try:
            yield conn
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()
    else:
        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# --------------------------------------------------------------------------- #
# 建表语句（两套方言）
# --------------------------------------------------------------------------- #
SCHEMA_SQLITE = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_vip INTEGER DEFAULT 0,
        vip_expire_at TEXT,
        daily_summary_count INTEGER DEFAULT 0,
        last_summary_date TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_no TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        currency TEXT DEFAULT 'cny',
        status TEXT DEFAULT 'pending',
        plan_type TEXT DEFAULT 'monthly',
        stripe_session_id TEXT UNIQUE,
        stripe_payment_intent_id TEXT,
        paid_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
    CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
    CREATE INDEX IF NOT EXISTS idx_orders_order_no ON orders(order_no);
    CREATE INDEX IF NOT EXISTS idx_orders_stripe_session_id ON orders(stripe_session_id);
"""

SCHEMA_MYSQL = """
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        email VARCHAR(255) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        is_vip TINYINT DEFAULT 0,
        vip_expire_at VARCHAR(64),
        daily_summary_count INT DEFAULT 0,
        last_summary_date VARCHAR(32),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

    CREATE TABLE IF NOT EXISTS orders (
        id INT AUTO_INCREMENT PRIMARY KEY,
        order_no VARCHAR(64) UNIQUE NOT NULL,
        user_id INT NOT NULL,
        amount INT NOT NULL,
        currency VARCHAR(8) DEFAULT 'cny',
        status VARCHAR(16) DEFAULT 'pending',
        plan_type VARCHAR(16) DEFAULT 'monthly',
        stripe_session_id VARCHAR(255) UNIQUE,
        stripe_payment_intent_id VARCHAR(255),
        paid_at VARCHAR(64),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_users_email_orders (user_id),
        INDEX idx_orders_order_no (order_no),
        INDEX idx_orders_session (stripe_session_id),
        CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def init_db():
    """初始化数据库表结构（自动适配 SQLite / MySQL）"""
    if USE_MYSQL:
        _ensure_mysql_database()
        with get_db() as conn:
            conn.executescript(SCHEMA_MYSQL)
    else:
        with get_db() as conn:
            conn.executescript(SCHEMA_SQLITE)


FREE_DAILY_SUMMARY_LIMIT = 3


# --------------------------------------------------------------------------- #
# 业务查询（方言无关，统一用 ? 占位符）
# --------------------------------------------------------------------------- #
def get_user_by_email(email: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def create_user(email: str, password_hash: str) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, password_hash),
        )
        return {"id": cursor.lastrowid, "email": email}


def check_and_increment_summary(user_id: int) -> tuple[bool, int]:
    """
    检查用户是否可以使用 AI 总结，并自增计数。
    返回 (allowed, remaining_count)
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return False, 0

        if user["is_vip"] and user["vip_expire_at"]:
            try:
                expire = datetime.fromisoformat(str(user["vip_expire_at"]))
            except ValueError:
                expire = None
            if expire and expire.tzinfo is None:
                expire = expire.replace(tzinfo=timezone.utc)
            if expire and expire > datetime.now(timezone.utc):
                return True, -1  # -1 means unlimited

        if user["last_summary_date"] != today:
            conn.execute(
                "UPDATE users SET daily_summary_count = 1, last_summary_date = ? WHERE id = ?",
                (today, user_id),
            )
            return True, FREE_DAILY_SUMMARY_LIMIT - 1

        current = user["daily_summary_count"]
        if current >= FREE_DAILY_SUMMARY_LIMIT:
            return False, 0

        conn.execute(
            "UPDATE users SET daily_summary_count = daily_summary_count + 1 WHERE id = ?",
            (user_id,),
        )
        return True, FREE_DAILY_SUMMARY_LIMIT - current - 1


def create_order(user_id: int, order_no: str, amount: int, currency: str = "cny", plan_type: str = "monthly") -> dict:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO orders (order_no, user_id, amount, currency, plan_type) VALUES (?, ?, ?, ?, ?)",
            (order_no, user_id, amount, currency, plan_type),
        )
        return {"order_no": order_no, "user_id": user_id, "amount": amount}


def update_order_stripe_session(order_no: str, session_id: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE orders SET stripe_session_id = ?, updated_at = datetime('now') WHERE order_no = ?",
            (session_id, order_no),
        )


def complete_order(session_id: str, payment_intent_id: str) -> dict | None:
    """
    支付完成时更新订单状态、激活 VIP。
    使用事务保证幂等：只有 pending 状态的订单才会被更新。
    """
    with get_db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE stripe_session_id = ? AND status = 'pending'",
            (session_id,),
        ).fetchone()

        if not order:
            return None

        now = datetime.now(timezone.utc).isoformat()

        from dateutil.relativedelta import relativedelta
        user = conn.execute("SELECT * FROM users WHERE id = ?", (order["user_id"],)).fetchone()

        current_expire = None
        if user["vip_expire_at"]:
            try:
                current_expire = datetime.fromisoformat(str(user["vip_expire_at"]))
            except ValueError:
                pass

        base_time = datetime.now(timezone.utc)
        if current_expire and current_expire.tzinfo is None:
            current_expire = current_expire.replace(tzinfo=timezone.utc)
        if current_expire and current_expire > base_time:
            base_time = current_expire

        if order["plan_type"] == "monthly":
            new_expire = base_time + relativedelta(months=1)
        elif order["plan_type"] == "yearly":
            new_expire = base_time + relativedelta(years=1)
        else:
            new_expire = base_time + relativedelta(months=1)

        conn.execute(
            "UPDATE orders SET status = 'paid', stripe_payment_intent_id = ?, paid_at = ?, updated_at = ? WHERE id = ?",
            (payment_intent_id, now, now, order["id"]),
        )

        conn.execute(
            "UPDATE users SET is_vip = 1, vip_expire_at = ?, updated_at = ? WHERE id = ?",
            (new_expire.isoformat(), now, order["user_id"]),
        )

        return dict(order)


def get_order_by_no(order_no: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_no = ?", (order_no,)).fetchone()
        return dict(row) if row else None


def get_user_orders(user_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
