import datetime as dt
from typing import Any

import aiosqlite


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    tg_id INTEGER PRIMARY KEY,
                    username TEXT,
                    name TEXT,
                    phone TEXT,
                    thread_id INTEGER,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS claims (
                    id TEXT PRIMARY KEY,
                    tg_id INTEGER,
                    description TEXT,
                    purchase_type TEXT,
                    purchase_value TEXT,
                    status TEXT,
                    manager_comment TEXT,
                    group_message_id INTEGER,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS claim_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id TEXT,
                    file_id TEXT,
                    file_type TEXT
                );

                CREATE TABLE IF NOT EXISTS claim_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id TEXT,
                    author TEXT,
                    text TEXT,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS warranties (
                    id TEXT PRIMARY KEY,
                    tg_id INTEGER,
                    cz_code TEXT,
                    cz_file_id TEXT,
                    receipt_file_id TEXT,
                    sku TEXT,
                    receipt_date TEXT,
                    receipt_text TEXT,
                    receipt_items TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS cz_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER,
                    cz_code TEXT,
                    created_at TEXT
                );
                """
            )
            
            # Migration: add thread_id if not exists
            try:
                await db.execute("ALTER TABLE users ADD COLUMN thread_id INTEGER")
            except aiosqlite.OperationalError:
                pass # already exists

            try:
                await db.execute("ALTER TABLE warranties ADD COLUMN receipt_items TEXT")
            except aiosqlite.OperationalError:
                pass # already exists
                
            try:
                await db.execute("ALTER TABLE claims ADD COLUMN group_message_id INTEGER")
            except aiosqlite.OperationalError:
                pass # already exists

            await db.commit()

    async def upsert_user(self, tg_id: int, username: str | None, name: str | None) -> None:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO users (tg_id, username, name, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tg_id) DO UPDATE SET
                    username=excluded.username,
                    name=COALESCE(excluded.name, users.name)
                """,
                (tg_id, username, name, now),
            )
            await db.commit()

    async def update_user_phone(self, tg_id: int, phone: str | None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE users SET phone=? WHERE tg_id=?", (phone, tg_id))
            await db.commit()

    async def update_user_thread(self, tg_id: int, thread_id: int | None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE users SET thread_id=? WHERE tg_id=?", (thread_id, tg_id))
            await db.commit()

    async def get_setting(self, key: str) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = await cur.fetchone()
            return row[0] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            await db.commit()

    async def get_user_by_thread(self, thread_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM users WHERE thread_id=?", (thread_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_user(self, tg_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def create_claim(
        self,
        claim_id: str,
        tg_id: int,
        description: str,
        purchase_type: str,
        purchase_value: str,
    ) -> None:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO claims
                (id, tg_id, description, purchase_type, purchase_value, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (claim_id, tg_id, description, purchase_type, purchase_value, "Новая", now, now),
            )
            await db.commit()

    async def add_claim_file(self, claim_id: str, file_id: str, file_type: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO claim_files (claim_id, file_id, file_type) VALUES (?, ?, ?)",
                (claim_id, file_id, file_type),
            )
            await db.commit()

    async def list_claims_by_user(self, tg_id: int | None, limit: int = 5) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            if tg_id:
                cur = await db.execute(
                    "SELECT * FROM claims WHERE tg_id=? ORDER BY created_at DESC LIMIT ?",
                    (tg_id, limit),
                )
            else:
                cur = await db.execute(
                    "SELECT * FROM claims ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

    async def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM claims WHERE id=?", (claim_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_claim_files(self, claim_id: str) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM claim_files WHERE claim_id=?", (claim_id,)
            )
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

    async def update_claim_status(self, claim_id: str, status: str) -> None:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE claims SET status=?, updated_at=? WHERE id=?",
                (status, now, claim_id),
            )
            await db.commit()

    async def update_claim_comment(self, claim_id: str, comment: str) -> None:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE claims SET manager_comment=?, updated_at=? WHERE id=?",
                (comment, now, claim_id),
            )
            await db.commit()

    async def update_claim_group_message(self, claim_id: str, message_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE claims SET group_message_id=? WHERE id=?",
                (message_id, claim_id),
            )
            await db.commit()

    async def add_claim_note(self, claim_id: str, author: str, text: str) -> None:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO claim_notes (claim_id, author, text, created_at) VALUES (?, ?, ?, ?)",
                (claim_id, author, text, now),
            )
            await db.commit()

    async def get_last_claim_by_status(self, tg_id: int, status: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM claims WHERE tg_id=? AND status=? ORDER BY updated_at DESC LIMIT 1",
                (tg_id, status),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def add_cz_code(self, tg_id: int, cz_code: str) -> None:
        now = dt.datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO cz_codes (tg_id, cz_code, created_at) VALUES (?, ?, ?)",
                (tg_id, cz_code, now),
            )
            await db.commit()

    async def create_warranty(
        self,
        warranty_id: str,
        tg_id: int,
        cz_code: str,
        cz_file_id: str,
        receipt_file_id: str,
        sku: str,
        receipt_date: str | None = None,
        receipt_text: str | None = None,
        receipt_items: str | None = None,
    ) -> tuple[str, str]:
        now = dt.datetime.utcnow()
        if receipt_date:
            try:
                # Expecting YYYY-MM-DD or DD.MM.YYYY
                if "." in receipt_date:
                    start_dt = dt.datetime.strptime(receipt_date, "%d.%m.%Y")
                else:
                    start_dt = dt.datetime.fromisoformat(receipt_date)
                start = start_dt.date()
            except Exception:
                start = now.date()
        else:
            start = now.date()

        end = start.replace(year=start.year + 1)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO warranties
                (id, tg_id, cz_code, cz_file_id, receipt_file_id, sku, receipt_date, receipt_text, receipt_items, start_date, end_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    warranty_id,
                    tg_id,
                    cz_code,
                    cz_file_id,
                    receipt_file_id,
                    sku,
                    receipt_date,
                    receipt_text,
                    receipt_items,
                    start.isoformat(),
                    end.isoformat(),
                    now.isoformat(),
                ),
            )
            await db.commit()
        return start.isoformat(), end.isoformat()

    async def has_warranty(self, tg_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT 1 FROM warranties WHERE tg_id=? LIMIT 1", (tg_id,))
            row = await cur.fetchone()
            return row is not None

    async def get_warranties(self, tg_id: int) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM warranties WHERE tg_id=?", (tg_id,))
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

    async def list_claims_with_threads(self, status: str | None = None, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            if status:
                cur = await db.execute(
                    "SELECT c.*, u.thread_id FROM claims c LEFT JOIN users u ON c.tg_id = u.tg_id WHERE c.status=? ORDER BY c.created_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                )
            else:
                cur = await db.execute(
                    "SELECT c.*, u.thread_id FROM claims c LEFT JOIN users u ON c.tg_id = u.tg_id ORDER BY c.created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

    async def count_claims(self, status: str | None = None) -> int:
        async with aiosqlite.connect(self.path) as db:
            if status:
                cur = await db.execute("SELECT COUNT(*) FROM claims WHERE status=?", (status,))
            else:
                cur = await db.execute("SELECT COUNT(*) FROM claims")
            row = await cur.fetchone()
            return row[0] if row else 0

    async def delete_user_data(self, tg_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM users WHERE tg_id=?", (tg_id,))
            await db.execute("DELETE FROM claims WHERE tg_id=?", (tg_id,))
            await db.execute("DELETE FROM warranties WHERE tg_id=?", (tg_id,))
            await db.execute("DELETE FROM cz_codes WHERE tg_id=?", (tg_id,))
            await db.commit()

