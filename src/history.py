"""Conversation history stored in Firestore.

This module replaces the previous local JSON storage and keeps a small
conversation history for each buyer inside the Firebase collection
``history``.  Each document uses the buyer name as its document ID and
stores an array field ``messages`` with the role and text of each message
and a ``order_info`` map with details of the related order.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple
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
    buyer_name: str,
    pairs: List[Tuple[str, str]],
    order_info: Dict[str, Any] | None = None,
    max_depth: int = 20,
) -> None:
    """Append conversation ``pairs`` and ``order_info`` to Firestore."""

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
    if order_info:
        order_fields = {
            k: {"stringValue": str(v)} for k, v in order_info.items() if v is not None
        }
        data["fields"]["order_info"] = {"mapValue": {"fields": order_fields}}

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


def fetch_all_histories() -> Dict[str, Dict[str, Any]]:
    """Return all conversation histories and order info stored in Firestore.

    The result is a mapping of ``buyer_name`` to a dict with keys
    ``messages`` (list of ``(role, text)`` tuples) and ``order_info`` (dict
    of order fields). If no documents are found or an error occurs, an empty
    dict is returned.
    """

    base_url = (
        f"{BASE_URL}/projects/{FIREBASE_CONFIG['projectId']}/databases/(default)/documents"
    )
    url = f"{base_url}/history?key={FIREBASE_CONFIG['apiKey']}"
    out: Dict[str, Dict[str, Any]] = {}
    page_token = ""
    try:
        while True:
            page_url = url + (f"&pageToken={page_token}" if page_token else "")
            with urlopen(page_url) as resp:
                data = json.load(resp)
            for doc in data.get("documents", []):
                name = doc.get("name", "").split("/")[-1]
                fields = doc.get("fields", {})
                values = (
                    fields.get("messages", {})
                    .get("arrayValue", {})
                    .get("values", [])
                )
                items: List[Tuple[str, str]] = []
                for v in values:
                    f = v.get("mapValue", {}).get("fields", {})
                    role = f.get("role", {}).get("stringValue", "")
                    text = f.get("text", {}).get("stringValue", "")
                    items.append((role, text))
                info_fields = (
                    fields.get("order_info", {})
                    .get("mapValue", {})
                    .get("fields", {})
                )
                order_info = {
                    k: v.get("stringValue", "") for k, v in info_fields.items()
                }
                out[name] = {"messages": items, "order_info": order_info}
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    except Exception:
        return {}
    return out

