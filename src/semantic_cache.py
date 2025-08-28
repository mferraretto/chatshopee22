from __future__ import annotations

"""Simple in-memory semantic cache for responses."""

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List


@dataclass
class CacheEntry:
    message: str
    etapa: str
    intent: str
    reply: str


class SemanticCache:
    def __init__(self) -> None:
        self.entries: List[CacheEntry] = []

    def get(self, message: str, etapa: str, intent: str, threshold: float = 0.9) -> str | None:
        for ent in self.entries:
            if ent.etapa == etapa and ent.intent == intent:
                if SequenceMatcher(None, ent.message, message).ratio() >= threshold:
                    return ent.reply
        return None

    def store(self, message: str, etapa: str, intent: str, reply: str, max_entries: int = 50) -> None:
        if not (message and reply):
            return
        self.entries.append(CacheEntry(message, etapa, intent, reply))
        if len(self.entries) > max_entries:
            self.entries = self.entries[-max_entries:]


_cache = SemanticCache()


def get_cached_reply(message: str, etapa: str, intent: str) -> str | None:
    return _cache.get(message, etapa, intent)


def store_cached_reply(message: str, etapa: str, intent: str, reply: str) -> None:
    _cache.store(message, etapa, intent, reply)
