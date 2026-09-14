"""Durable personal accounts; no Telegram or bookmaker side effects.

Money is stored as integer kopecks. Bank entries are snapshots of the current
available balance: entering it again does not subtract old stakes a second time.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


def money(value) -> int:
    try:
        number = Decimal(str(value).replace(",", "."))
        if not number.is_finite() or number <= 0 or number > Decimal("1000000000"):
            raise ValueError("Введите положительную сумму не больше 1 млрд ₽.")
        cents = int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if cents <= 0:
            raise ValueError("Минимальная сумма — 0,01 ₽.")
        return cents
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Введите сумму числом.") from exc


def decimal_odds(value) -> float:
    try:
        result = float(str(value).replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise ValueError("Введите коэффициент числом.") from exc
    if not math.isfinite(result) or not 1 < result <= 1000000:
        raise ValueError("Коэффициент должен быть больше 1 и не больше 1 000 000.")
    return result


class Store:
    def __init__(self, path: str, timezone: str = "Asia/Yekaterinburg"):
        self.zone = ZoneInfo(timezone)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS banks (
                user_id INTEGER NOT NULL, day TEXT NOT NULL, amount INTEGER NOT NULL,
                loss_limit INTEGER, ledger_cursor INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, day)
            );
            CREATE TABLE IF NOT EXISTS recommendations (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, session_id TEXT NOT NULL,
                created_at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS recommendations_user ON recommendations(user_id, created_at);
            CREATE TABLE IF NOT EXISTS cart (
                user_id INTEGER NOT NULL, rec_id TEXT NOT NULL REFERENCES recommendations(id),
                day TEXT NOT NULL, stake INTEGER NOT NULL, odds REAL,
                PRIMARY KEY(user_id, rec_id)
            );
            CREATE TABLE IF NOT EXISTS bets (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                rec_id TEXT NOT NULL REFERENCES recommendations(id),
                created_at TEXT NOT NULL, day TEXT NOT NULL,
                stake INTEGER NOT NULL, odds REAL NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', payout INTEGER NOT NULL DEFAULT 0,
                settled_at TEXT, settled_day TEXT, UNIQUE(user_id, rec_id)
            );
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                bet_id TEXT NOT NULL REFERENCES bets(id), amount INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS searches (
                user_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL,
                mode TEXT NOT NULL, sports TEXT NOT NULL, event_id TEXT,
                status TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS event_snapshots (
                user_id INTEGER NOT NULL, event_id TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(user_id,event_id)
            );
        """)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def today(self) -> str:
        return self.now().astimezone(self.zone).date().isoformat()

    def set_bank(self, user_id: int, amount: float, loss_limit: float | None = None) -> dict:
        cents = money(amount)
        limit = money(loss_limit) if loss_limit is not None else None
        with self.db:
            cursor = self.db.execute("SELECT COALESCE(MAX(id),0) FROM ledger WHERE user_id=?", (user_id,)).fetchone()[0]
            self.db.execute("INSERT OR REPLACE INTO banks VALUES (?,?,?,?,?)", (user_id, self.today(), cents, limit, cursor))
        return self.get_bank(user_id)

    def get_bank(self, user_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM banks WHERE user_id=? AND day=?", (user_id, self.today())).fetchone()
        if row is None:
            return None
        change = self.db.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE user_id=? AND id>?", (user_id, row["ledger_cursor"])).fetchone()[0]
        pnl = self.db.execute("SELECT COALESCE(SUM(payout-stake),0) FROM bets WHERE user_id=? AND settled_day=? AND status!='pending'", (user_id, self.today())).fetchone()[0]
        exposure = self.db.execute("SELECT COALESCE(SUM(stake),0) FROM bets WHERE user_id=? AND status='pending'", (user_id,)).fetchone()[0]
        return {"amount": row["amount"] / 100, "available": (row["amount"] + change) / 100,
                "loss_limit": row["loss_limit"] / 100 if row["loss_limit"] else None,
                "day": row["day"], "pnl": pnl / 100, "exposure": exposure / 100}

    def save_recommendations(self, user_id: int, session_id: str, items: list[dict]) -> list[dict]:
        saved = []
        now = self.now().isoformat()
        with self.db:
            for item in items:
                payload = dict(item)
                payload["key"] = payload.get("key") or payload.get("id")
                payload["id"] = uuid4().hex[:16]
                payload["created_at"] = now
                payload["session_id"] = session_id
                self.db.execute("INSERT INTO recommendations VALUES (?,?,?,?,?)", (payload["id"], user_id, session_id, now, json.dumps(payload, ensure_ascii=False, allow_nan=False)))
                saved.append(payload)
        return saved

    def get_recommendation(self, user_id: int, rec_id: str) -> dict | None:
        row = self.db.execute("SELECT payload FROM recommendations WHERE user_id=? AND id=?", (user_id, rec_id)).fetchone()
        return json.loads(row[0]) if row else None

    def list_recommendations(self, user_id: int, limit: int = 5, offset: int = 0) -> list[dict]:
        rows = self.db.execute("SELECT payload FROM recommendations WHERE user_id=? ORDER BY created_at DESC, rowid ASC LIMIT ? OFFSET ?", (user_id, max(1, min(limit, 100)), max(0, offset)))
        return [json.loads(row[0]) for row in rows]

    def latest_recommendations(self, user_id: int, limit: int = 5, offset: int = 0) -> list[dict]:
        last = self.db.execute("SELECT session_id FROM recommendations WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (user_id,)).fetchone()
        if not last:
            return []
        rows = self.db.execute("SELECT payload FROM recommendations WHERE user_id=? AND session_id=? ORDER BY rowid ASC LIMIT ? OFFSET ?", (user_id, last[0], max(1, min(limit, 100)), max(0, offset)))
        return [json.loads(row[0]) for row in rows]

    def save_event(self, user_id: int, event_id: str, payload: dict) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO event_snapshots VALUES (?,?,?)", (user_id, event_id, json.dumps(payload, ensure_ascii=False)))

    def get_event(self, user_id: int, event_id: str) -> dict | None:
        row = self.db.execute("SELECT payload FROM event_snapshots WHERE user_id=? AND event_id=?", (user_id, event_id)).fetchone()
        return json.loads(row[0]) if row else None

    def add_to_cart(self, user_id: int, rec_id: str) -> None:
        item = self.get_recommendation(user_id, rec_id)
        if item is None:
            raise ValueError("Рекомендация не найдена. Запустите новый поиск.")
        if self.get_bank(user_id) is None:
            raise ValueError("Сначала укажите доступный банк на сегодня.")
        if self.db.execute("SELECT 1 FROM bets WHERE user_id=? AND rec_id=?", (user_id, rec_id)).fetchone():
            raise ValueError("Эта ставка уже записана.")
        stake = money(item["stake"]) if item.get("stake", 0) > 0 else 0
        odds = decimal_odds(item["odds"]) if item.get("odds") else None
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO cart VALUES (?,?,?,?,?)", (user_id, rec_id, self.today(), stake, odds))

    def cart(self, user_id: int) -> list[dict]:
        rows = self.db.execute("SELECT c.*,r.payload FROM cart c JOIN recommendations r ON r.id=c.rec_id WHERE c.user_id=? ORDER BY c.rowid", (user_id,))
        return [dict(json.loads(row["payload"]), stake=row["stake"] / 100, odds=row["odds"], bank_day=row["day"]) for row in rows]

    def edit_cart(self, user_id: int, rec_id: str, stake: float, odds: float) -> None:
        cents, price = money(stake), decimal_odds(odds)
        if self.get_bank(user_id) is None:
            raise ValueError("Сначала укажите доступный банк на сегодня.")
        with self.db:
            changed = self.db.execute("UPDATE cart SET stake=?,odds=?,day=? WHERE user_id=? AND rec_id=?", (cents, price, self.today(), user_id, rec_id)).rowcount
            if not changed:
                raise ValueError("Ставки нет в выбранных.")

    def remove_from_cart(self, user_id: int, rec_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM cart WHERE user_id=? AND rec_id=?", (user_id, rec_id))

    def confirm_cart(self, user_id: int) -> list[dict]:
        # No await between validation and atomic commit; repeated callbacks cannot
        # double-book a stake. User IDs are part of every read/write predicate.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            bank = self.get_bank(user_id)
            items = self.cart(user_id)
            if not bank:
                raise ValueError("Укажите доступный банк на сегодня.")
            if not items:
                raise ValueError("Сначала выберите ставки.")
            if any(item["bank_day"] != self.today() for item in items):
                raise ValueError("Наступил новый день. Подтвердите суммы выбранных ставок заново.")
            total = 0
            for item in items:
                total += money(item["stake"])
                decimal_odds(item["odds"])
            if total > int(round(bank["available"] * 100)):
                raise ValueError("Общая сумма больше доступного банка. Измените суммы или уточните банк.")
            placed = []
            stamp = self.now().isoformat()
            for item in items:
                bet_id = uuid4().hex[:16]
                cents = money(item["stake"])
                self.db.execute("INSERT INTO bets (id,user_id,rec_id,created_at,day,stake,odds,payload) VALUES (?,?,?,?,?,?,?,?)", (bet_id, user_id, item["id"], stamp, self.today(), cents, item["odds"], json.dumps(item, ensure_ascii=False, allow_nan=False)))
                self.db.execute("INSERT INTO ledger(user_id,bet_id,amount,created_at) VALUES (?,?,?,?)", (user_id, bet_id, -cents, stamp))
                placed.append(dict(item, id=bet_id, rec_id=item["id"], status="pending", pnl=None))
            self.db.execute("DELETE FROM cart WHERE user_id=?", (user_id,))
            self.db.execute("UPDATE searches SET status='stopped',updated_at=? WHERE user_id=?", (stamp, user_id))
        return placed

    def history(self, user_id: int, limit: int = 10, offset: int = 0) -> list[dict]:
        rows = self.db.execute("SELECT * FROM bets WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?", (user_id, max(1, min(limit, 100)), max(0, offset)))
        return [dict(json.loads(row["payload"]), id=row["id"], rec_id=row["rec_id"], stake=row["stake"] / 100,
                     odds=row["odds"], status=row["status"], created_at=row["created_at"], settled_at=row["settled_at"],
                     pnl=(row["payout"] - row["stake"]) / 100 if row["status"] != "pending" else None) for row in rows]

    def settle(self, user_id: int, bet_id: str, status: str) -> None:
        if status not in {"won", "lost", "void"}:
            raise ValueError("Неизвестный результат ставки.")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM bets WHERE user_id=? AND id=?", (user_id, bet_id)).fetchone()
            if not row:
                raise ValueError("Ставка не найдена.")
            if row["status"] != "pending":
                raise ValueError("Результат этой ставки уже записан.")
            payout = int((Decimal(row["stake"]) * Decimal(str(row["odds"]))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) if status == "won" else row["stake"] if status == "void" else 0
            stamp = self.now().isoformat()
            self.db.execute("UPDATE bets SET status=?,payout=?,settled_at=?,settled_day=? WHERE user_id=? AND id=?", (status, payout, stamp, self.today(), user_id, bet_id))
            self.db.execute("INSERT INTO ledger(user_id,bet_id,amount,created_at) VALUES (?,?,?,?)", (user_id, bet_id, payout, stamp))

    def save_search(self, user_id: int, chat_id: int, sports: list[str], mode: str, event_id: str | None) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO searches VALUES (?,?,?,?,?,?,?)", (user_id, chat_id, mode, json.dumps(sports), event_id, "running", self.now().isoformat()))

    def stop_search(self, user_id: int) -> None:
        with self.db:
            self.db.execute("UPDATE searches SET status='stopped',updated_at=? WHERE user_id=?", (self.now().isoformat(), user_id))

    def interrupt_searches(self) -> list[dict]:
        rows = [dict(row) for row in self.db.execute("SELECT * FROM searches WHERE status='running'")]
        with self.db:
            self.db.execute("UPDATE searches SET status='interrupted' WHERE status='running'")
        return rows

    def close(self) -> None:
        self.db.close()
