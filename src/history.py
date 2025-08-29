"""Conversation history stored in Firestore.

This module replaces the previous local JSON storage and keeps a small
conversation history for each buyer inside the Firebase collection
``history``.  Each document uses the buyer name as its document ID and
contains an array field called ``messages`` with the role and text of each
message.
"""

from __future__ import annotations

import json
from typing import List, Tuple
from urllib.request import Request, urlopen

from .firebase_client import FIREBASE_CONFIG


BASE_URL = "https://firestore.googleapis.com/v1"


def _doc_id(name: str) -> str:
    """Generate a safe Firestore document ID from the buyer name."""
    return name.replace("/", "_")


def get_history(buyer_name: str) -> List[Tuple[str, str]]:
    """Return the stored conversation history for ``buyer_name``.

    Each entry is returned as ``(role, text)``.  If the document does not
    exist or any error occurs, an empty list is returned.
    """

    if not buyer_name:
        return []
    doc_id = _doc_id(buyer_name)
    url = (
        f"{BASE_URL}/projects/{FIREBASE_CONFIG['projectId']}/databases/(default)/documents/"
        f"history/{doc_id}?key={FIREBASE_CONFIG['apiKey']}"
    )
    try:
        with urlopen(url) as resp:
            data = json.load(resp)
        values = (
            data.get("fields", {})
            .get("messages", {})
            .get("arrayValue", {})
            .get("values", [])
        )
        out: List[Tuple[str, str]] = []
        for v in values:
            f = v.get("mapValue", {}).get("fields", {})
            role = f.get("role", {}).get("stringValue", "")
            text = f.get("text", {}).get("stringValue", "")
            out.append((role, text))
        return out
    except Exception:
        return []


def append_history(
    buyer_name: str, pairs: List[Tuple[str, str]], max_depth: int = 20
) -> None:
    """Append conversation ``pairs`` to the buyer history in Firestore."""

    if not buyer_name:
        return
    items = get_history(buyer_name)
    for role, text in pairs:
        entry = (role, text)
        if entry not in items:
            items.append(entry)
    items = items[-max_depth:]

    doc_id = _doc_id(buyer_name)
    url = (
        f"{BASE_URL}/projects/{FIREBASE_CONFIG['projectId']}/databases/(default)/documents/"
        f"history/{doc_id}?key={FIREBASE_CONFIG['apiKey']}"
    )

    values = [
        {
            "mapValue": {
                "fields": {
                    "role": {"stringValue": role},
                    "text": {"stringValue": text},
                }
            }
        }
        for role, text in items
    ]
    data = {"fields": {"messages": {"arrayValue": {"values": values}}}}

    req = Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urlopen(req) as resp:
            resp.read()
    except Exception:
        pass

