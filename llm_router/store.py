from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("gateway")

try:
    from cryptography.fernet import Fernet
except ModuleNotFoundError:
    Fernet = None  # type: ignore[misc, assignment]


@dataclass(slots=True)
class Message:
    id: str
    role: str
    content: str
    model: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "model": self.model,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        return cls(
            id=data["id"],
            role=data["role"],
            content=data["content"],
            model=data.get("model"),
            created_at=float(data.get("created_at", time.time())),
        )


@dataclass(slots=True)
class Conversation:
    id: str
    title: str
    created_at: float
    updated_at: float
    messages: list[Message]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Conversation:
        return cls(
            id=data["id"],
            title=data.get("title", "Untitled"),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
        )


class ConversationStore:
    """Simple file-backed conversation store.

    Conversations are kept in memory and persisted to a JSON file on every
    write. Use a single instance per process to avoid race conditions.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(
            path or os.environ.get("CONVERSATIONS_PATH", ".cache/conversations.json")
        )
        self._lock = asyncio.Lock()
        self._cache: dict[str, Conversation] = {}
        self._loaded = False
        self._fernet = self._make_fernet(os.environ.get("CONVERSATION_ENCRYPTION_KEY"))

    async def _load(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            try:
                text = self.path.read_text(encoding="utf-8")
                payload = json.loads(self._decrypt(text))
                for item in payload.get("conversations", []):
                    try:
                        conv = Conversation.from_dict(item)
                        self._cache[conv.id] = conv
                    except (KeyError, ValueError, TypeError):
                        continue
            except (OSError, json.JSONDecodeError, ValueError):
                pass
            self._loaded = True

    async def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = {
            "conversations": [c.to_dict() for c in self._cache.values()],
            "saved_at": time.time(),
        }
        text = json.dumps(data, ensure_ascii=False, indent=2)
        tmp.write_text(self._encrypt(text), encoding="utf-8")
        tmp.replace(self.path)

    async def create(
        self, title: str = "Untitled", system_prompt: str | None = None
    ) -> Conversation:
        await self._load()
        conv_id = str(uuid.uuid4())
        now = time.time()
        messages: list[Message] = []
        if system_prompt:
            messages.append(
                Message(
                    id=str(uuid.uuid4()),
                    role="system",
                    content=system_prompt,
                )
            )
        conversation = Conversation(
            id=conv_id,
            title=title,
            created_at=now,
            updated_at=now,
            messages=messages,
        )
        async with self._lock:
            self._cache[conv_id] = conversation
            await self._save()
        return conversation

    async def get(self, conversation_id: str) -> Conversation | None:
        await self._load()
        return self._cache.get(conversation_id)

    async def list(self, limit: int = 100, offset: int = 0) -> list[Conversation]:
        await self._load()
        sorted_convs = sorted(
            self._cache.values(), key=lambda c: c.updated_at, reverse=True
        )
        return sorted_convs[offset : offset + limit]

    async def search(self, query: str, limit: int = 50) -> list[Conversation]:
        await self._load()
        q = query.lower().strip()
        if not q:
            return await self.list(limit=limit)
        matched: list[Conversation] = []
        for conv in sorted(
            self._cache.values(), key=lambda c: c.updated_at, reverse=True
        ):
            if q in conv.title.lower():
                matched.append(conv)
                continue
            for message in conv.messages:
                if q in message.content.lower():
                    matched.append(conv)
                    break
        return matched[:limit]

    async def delete(self, conversation_id: str) -> bool:
        await self._load()
        async with self._lock:
            if conversation_id not in self._cache:
                return False
            del self._cache[conversation_id]
            await self._save()
        return True

    async def append_message(
        self, conversation_id: str, message: Message, *, update_title: bool = False
    ) -> Conversation | None:
        await self._load()
        async with self._lock:
            conv = self._cache.get(conversation_id)
            if not conv:
                return None
            conv.messages.append(message)
            conv.updated_at = time.time()
            if update_title and (not conv.title or conv.title == "Untitled"):
                if message.role == "user":
                    conv.title = message.content[:60]
            await self._save()
            return conv

    async def update_system_prompt(
        self, conversation_id: str, system_prompt: str | None
    ) -> Conversation | None:
        await self._load()
        async with self._lock:
            conv = self._cache.get(conversation_id)
            if not conv:
                return None
            # Remove existing system message(s).
            conv.messages = [m for m in conv.messages if m.role != "system"]
            if system_prompt:
                conv.messages.insert(
                    0,
                    Message(
                        id=str(uuid.uuid4()),
                        role="system",
                        content=system_prompt,
                    ),
                )
            conv.updated_at = time.time()
            await self._save()
            return conv

    def _make_fernet(self, raw_key: str | None) -> Any:
        if not raw_key:
            return None
        if Fernet is None:
            raise RuntimeError(
                "cryptography is required when CONVERSATION_ENCRYPTION_KEY is set"
            )
        key = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode()).digest())
        return Fernet(key)

    def _encrypt(self, text: str) -> str:
        if self._fernet is None:
            return text
        return self._fernet.encrypt(text.encode("utf-8")).decode("ascii")

    def _decrypt(self, text: str) -> str:
        if self._fernet is None:
            return text
        stripped = text.strip()
        if stripped.startswith("{"):
            logger.warning(
                "Encryption key set but conversation store appears unencrypted; loading plain"
            )
            return text
        try:
            return self._fernet.decrypt(stripped.encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise RuntimeError("Could not decrypt conversation store") from exc

    async def close(self) -> None:
        async with self._lock:
            if self._cache:
                await self._save()
