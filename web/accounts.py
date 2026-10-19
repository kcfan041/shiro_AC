"""網頁帳號：暱稱＋密碼（PBKDF2 雜湊），存在 data/accounts.json。session 只放記憶體（重啟要重新登入）。"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time

ITER = 120_000
SESSION_TTL = 7 * 24 * 3600


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITER).hex()


class Accounts:
    def __init__(self, path: str, gm_names: list[str] | None = None):
        self.path = path
        self.gm_names = {n.strip().lower() for n in (gm_names or [])}
        self.users: dict[str, dict] = {}
        self.sessions: dict[str, tuple[str, float]] = {}
        if os.path.exists(path):
            with open(path, encoding="utf8") as f:
                self.users = json.load(f)

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf8") as f:
            json.dump(self.users, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def valid_name(name: str) -> bool:
        return 1 <= len(name) <= 16 and not any(c in name for c in "<>\"'&/\\")

    def register(self, name: str, password: str) -> tuple[bool, str]:
        name = name.strip()
        if not self.valid_name(name):
            return False, "暱稱 1～16 字，不能有特殊符號。"
        if len(password) < 4:
            return False, "密碼至少 4 個字。"
        if any(u["name"].lower() == name.lower() for u in self.users.values()):
            return False, "這個暱稱已經有人用了。"
        uid = f"w{secrets.randbelow(10 ** 9):09d}"
        while uid in self.users:
            uid = f"w{secrets.randbelow(10 ** 9):09d}"
        salt = secrets.token_bytes(16)
        self.users[uid] = {"name": name, "salt": salt.hex(), "hash": _hash(password, salt), "created": time.time()}
        self._save()
        return True, uid

    def login(self, name: str, password: str) -> str | None:
        name = name.strip().lower()
        for uid, u in self.users.items():
            if u["name"].lower() == name:
                if hmac.compare_digest(u["hash"], _hash(password, bytes.fromhex(u["salt"]))):
                    token = secrets.token_urlsafe(24)
                    self.sessions[token] = (uid, time.time() + SESSION_TTL)
                    return token
                return None
        return None

    def user_of(self, token: str | None) -> str | None:
        if not token:
            return None
        s = self.sessions.get(token)
        if s is None:
            return None
        if s[1] < time.time():
            self.sessions.pop(token, None)
            return None
        return s[0]

    def logout(self, token: str):
        self.sessions.pop(token, None)

    def name(self, uid: str) -> str:
        return self.users.get(uid, {}).get("name", uid)

    def is_gm(self, uid: str | None) -> bool:
        return bool(uid) and self.name(uid).lower() in self.gm_names
