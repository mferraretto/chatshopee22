from __future__ import annotations
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

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


TRIGGERS = {
    "reembolso parcial": ["reembolso parcial"],
    "enviar nova peça": ["nova peça", "nova peca"],
    "enviar peça faltante": [
        "peça faltante",
        "peca faltante",
        "peça faltando",
        "peca faltando",
    ],
}


def infer_problema(buyer_msgs: List[str]) -> str:
    """Infere a categoria do problema a partir da última mensagem do cliente.

    Apenas registra solicitações de reembolso parcial ou de envio de peças
    (nova ou faltante). Se não houver correspondência, retorna string vazia.
    """
    if not buyer_msgs:
        return ""

    last = buyer_msgs[-1].strip().lower()
    for label, kws in TRIGGERS.items():
        for kw in kws:
            if kw in last:
                return label
    return ""


def append_row(order_info: Dict[str, Any], buyer_only: List[str]) -> None:
    problema = infer_problema(buyer_only)
    if not problema:
        return

    _ensure_header()
    ultima_msg = buyer_only[-1].strip().replace("\n", " ") if buyer_only else ""

    row = [
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        order_info.get("orderId", ""),
        order_info.get("status", ""),
        order_info.get("title", ""),
        order_info.get("variation", ""),
        order_info.get("sku", ""),
        problema,
        ultima_msg,
    ]

    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)
