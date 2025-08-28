"""Conversation history stored in Firestore with additional context.

This module replaces the previous local JSON storage and keeps a small
conversation history for each buyer inside the Firebase collection
``history``.  Each document uses the buyer name as its document ID and
contains an array field called ``messages`` with the role and text of each
message.  Two extra fields are optionally stored:

``summary`` – factual TL;DR of the conversation history; and
``context`` – a map with slots about the order (pedido, sku, produto,
``status_logistico`` and etapa).
"""

from __future__ import annotations

import json
from typing import Dict, List, Tuple, Optional
from urllib.request import Request, urlopen
from pathlib import Path

from openpyxl import Workbook

from .firebase_client import FIREBASE_CONFIG
from .gemini_client import summarize_history


BASE_URL = "https://firestore.googleapis.com/v1"


def _doc_id(name: str) -> str:
    """Generate a safe Firestore document ID from the buyer name."""
    return name.replace("/", "_")


def get_history_with_summary(buyer_name: str) -> Tuple[List[Tuple[str, str]], str]:
    """Return the stored conversation history and summary for ``buyer_name``."""

    if not buyer_name:
        return [], ""
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
        summary = (
            data.get("fields", {}).get("summary", {}).get("stringValue", "")
        )
        return out, summary
    except Exception:
        return [], ""


def get_history(buyer_name: str) -> List[Tuple[str, str]]:
    """Backward compatible helper returning only messages."""

    msgs, _ = get_history_with_summary(buyer_name)
    return msgs


def get_context(buyer_name: str) -> Dict[str, str]:
    """Return stored context slots for ``buyer_name``."""

    if not buyer_name:
        return {}
    doc_id = _doc_id(buyer_name)
    url = (
        f"{BASE_URL}/projects/{FIREBASE_CONFIG['projectId']}/databases/(default)/documents/"
        f"history/{doc_id}?key={FIREBASE_CONFIG['apiKey']}"
    )
    try:
        with urlopen(url) as resp:
            data = json.load(resp)
        ctx_fields = (
            data.get("fields", {}).get("context", {}).get("mapValue", {}).get("fields", {})
        )
        return {k: v.get("stringValue", "") for k, v in ctx_fields.items()}
    except Exception:
        return {}


def append_history(
    buyer_name: str,
    pairs: List[Tuple[str, str]],
    max_depth: int = 20,
    summary_trigger: int = 40,
) -> None:
    """Append conversation ``pairs`` to the buyer history in Firestore.

    If the message count exceeds ``summary_trigger`` a factual TL;DR is
    generated and stored under the ``summary`` field.
    """

    if not buyer_name:
        return
    items, summary = get_history_with_summary(buyer_name)
    for role, text in pairs:
        entry = (role, text)
        if entry not in items:
            items.append(entry)
    items = items[-max_depth:]

    if len(items) >= summary_trigger:
        new_summary = summarize_history(items)
        if new_summary:
            summary = new_summary

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
    data_fields = {"messages": {"arrayValue": {"values": values}}}
    if summary:
        data_fields["summary"] = {"stringValue": summary}
    data = {"fields": data_fields}

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


def update_context(buyer_name: str, context: Dict[str, str]) -> None:
    """Update conversation context slots for ``buyer_name``."""

    if not buyer_name or not context:
        return
    doc_id = _doc_id(buyer_name)
    url = (
        f"{BASE_URL}/projects/{FIREBASE_CONFIG['projectId']}/databases/(default)/documents/"
        f"history/{doc_id}?key={FIREBASE_CONFIG['apiKey']}"
    )
    ctx_fields = {k: {"stringValue": v} for k, v in context.items()}
    data = {"fields": {"context": {"mapValue": {"fields": ctx_fields}}}}
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


def export_history_to_excel() -> Optional[Path]:
    """Export all conversation history documents to an Excel file."""

    url = (
        f"{BASE_URL}/projects/{FIREBASE_CONFIG['projectId']}/databases/(default)/documents/"
        f"history?key={FIREBASE_CONFIG['apiKey']}"
    )
    try:
        with urlopen(url) as resp:
            data = json.load(resp)
    except Exception:
        return None

    docs = data.get("documents", [])
    if not docs:
        return None

    wb = Workbook()
    ws = wb.active
    ws.append(["buyer_name", "role", "text"])

    for doc in docs:
        doc_name = doc.get("name", "")
        buyer = doc_name.split("/")[-1].replace("_", "/")
        messages = (
            doc.get("fields", {})
            .get("messages", {})
            .get("arrayValue", {})
            .get("values", [])
        )
        for msg in messages:
            fields = msg.get("mapValue", {}).get("fields", {})
            role = fields.get("role", {}).get("stringValue", "")
            text = fields.get("text", {}).get("stringValue", "")
            ws.append([buyer, role, text])

    path = Path("data/history.xlsx")
    path.parent.mkdir(exist_ok=True)
    wb.save(path)
    return path

