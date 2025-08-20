from __future__ import annotations
from pathlib import Path
import csv
from datetime import datetime, timezone
from typing import List, Dict, Any

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CSV_PATH = DATA_DIR / "atendimentos.csv"

HEADER = [
    "timestamp_utc",
    "order_id",
    "status",
    "produto",
    "variacao",
    "sku",
    "problema",
    "ultima_msg_comprador",
]


def _ensure_header():
    if not CSV_PATH.exists():
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(HEADER)


def infer_problema(buyer_msgs: List[str]) -> str:
    """
    Heurística simples: usa a última mensagem do cliente.
    (Depois dá pra trocar por classificador, regras, etc.)
    """
    if not buyer_msgs:
        return ""
    return buyer_msgs[-1].strip().replace("\n", " ")


def append_row(order_info: Dict[str, Any], buyer_only: List[str]):
    _ensure_header()
    row = [
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        order_info.get("orderId", ""),
        order_info.get("status", ""),
        order_info.get("title", ""),
        order_info.get("variation", ""),
        order_info.get("sku", ""),
        infer_problema(buyer_only),
        buyer_only[-1].strip().replace("\n", " ") if buyer_only else "",
    ]
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(row)
