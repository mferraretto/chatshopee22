import json
from typing import List, Tuple
from urllib.request import Request, urlopen

from .firebase_client import FIREBASE_CONFIG

BASE_URL = "https://firestore.googleapis.com/v1"
COLLECTION = "history"


def _doc_id(buyer_name: str) -> str:
    """Sanitize buyer name to be used as Firestore document id."""
    return buyer_name.replace("/", "_")


def get_history(buyer_name: str) -> List[Tuple[str, str]]:
    """Retrieve conversation history for a buyer from Firestore."""
    if not buyer_name:
        return []
    url = (
        f"{BASE_URL}/projects/{FIREBASE_CONFIG['projectId']}/databases/(default)/documents/"
        f"{COLLECTION}/{_doc_id(buyer_name)}?key={FIREBASE_CONFIG['apiKey']}"
    )
    try:
        with urlopen(url) as resp:
            data = json.load(resp)
        raw = (
            data.get("fields", {})
            .get("conversation", {})
            .get("stringValue", "")
        )
        if raw:
            items = json.loads(raw)
            return [(d.get("role", ""), d.get("text", "")) for d in items]
    except Exception:
        pass
    return []


def append_history(
    buyer_name: str, pairs: List[Tuple[str, str]], max_depth: int = 20
) -> None:
    """Append conversation pairs to buyer history in Firestore."""
    if not buyer_name:
        return

    history = get_history(buyer_name)
    for role, text in pairs:
        entry = {"role": role, "text": text}
        if entry not in history:
            history.append(entry)
    history = history[-max_depth:]

    payload = {
        "fields": {
            "conversation": {
                "stringValue": json.dumps(history, ensure_ascii=False)
            }
        }
    }
    body = json.dumps(payload).encode("utf-8")

    doc_id = _doc_id(buyer_name)
    patch_url = (
        f"{BASE_URL}/projects/{FIREBASE_CONFIG['projectId']}/databases/(default)/documents/"
        f"{COLLECTION}/{doc_id}?key={FIREBASE_CONFIG['apiKey']}"
    )
    req = Request(
        patch_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urlopen(req) as resp:
            resp.read()
        return
    except Exception:
        pass

    create_url = (
        f"{BASE_URL}/projects/{FIREBASE_CONFIG['projectId']}/databases/(default)/documents/"
        f"{COLLECTION}?documentId={doc_id}&key={FIREBASE_CONFIG['apiKey']}"
    )
    req = Request(create_url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req) as resp:
            resp.read()
    except Exception:
        pass

