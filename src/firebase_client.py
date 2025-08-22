import json
from typing import Dict
from urllib.request import urlopen, Request

FIREBASE_CONFIG = {
    "apiKey": "AIzaSyCFQGW574J5Y2N8xq1y-pLzNlLIptEn8_8",
    "projectId": "chatshopee-5e5a8",
}


def get_product_by_sku(sku: str) -> Dict[str, str]:
    """Fetch product information from Firestore by SKU."""
    if not sku:
        return {}

    base = "https://firestore.googleapis.com/v1"
    url = (
        f"{base}/projects/{FIREBASE_CONFIG['projectId']}/databases/(default)/documents/"
        f"products/{sku}?key={FIREBASE_CONFIG['apiKey']}"
    )
    try:
        with urlopen(url) as resp:
            data = json.load(resp)
        fields = data.get("fields", {})
        return {
            "nome": fields.get("nome", {}).get("stringValue", ""),
            "sku": fields.get("sku", {}).get("stringValue", sku),
            "descricao": fields.get("descricao", {}).get("stringValue", ""),
            "medidas": fields.get("medidas", {}).get("stringValue", ""),
        }
    except Exception:
        return {}


def save_case_document(case: Dict[str, str]) -> None:
    """Save a case record to Firestore using the REST API."""
    base = "https://firestore.googleapis.com/v1"
    doc_id = case.get("order_id", "")
    url = (
        f"{base}/projects/{FIREBASE_CONFIG['projectId']}/databases/(default)/documents/"
        f"atendimentos?documentId={doc_id}&key={FIREBASE_CONFIG['apiKey']}"
    )
    data = {"fields": {k: {"stringValue": v} for k, v in case.items()}}
    req = Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req) as resp:
            resp.read()
    except Exception:
        pass

