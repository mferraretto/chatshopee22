# src/complaints_storage.py

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any
from dataclasses import asdict

from .complaint_detector import ComplaintInfo
from .firebase_client import save_case_document

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

COMPLAINTS_CSV_PATH = DATA_DIR / "reclamacoes_detectadas.csv"
COMPLAINTS_JSON_PATH = DATA_DIR / "reclamacoes_detectadas.json"

COMPLAINTS_HEADER = [
    "timestamp_utc",
    "detected_at",
    "order_id", 
    "buyer_name",
    "produto",
    "variacao", 
    "sku",
    "order_status",
    "payment_amount",
    "payment_currency",
    "payment_method",
    "payment_time",
    "total_orders",
    "complaint_type",
    "confidence",
    "keywords_found",
    "relevant_messages",
    "status",
    "marked_for_review",
    "notes"
]


def _ensure_complaints_header():
    """Garante que o arquivo CSV de reclamações tem o cabeçalho"""
    if not COMPLAINTS_CSV_PATH.exists():
        with COMPLAINTS_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(COMPLAINTS_HEADER)


def save_complaints(complaints: List[ComplaintInfo], order_info: Dict[str, Any] = None) -> None:
    """Salva reclamações detectadas no CSV e JSON"""
    if not complaints:
        return
    
    _ensure_complaints_header()
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    
    # Prepara dados para salvar
    rows_to_save = []
    json_entries = []
    
    for complaint in complaints:
        # Dados do pedido principal (primeiro pedido)
        primary_order = order_info.get("primaryOrder", {}) if order_info else {}
        order_id = primary_order.get("orderId", "") or order_info.get("orderId", "") if order_info else ""
        buyer_name = order_info.get("buyer_name", "") if order_info else ""
        produto = primary_order.get("title", "") or order_info.get("title", "") if order_info else ""
        variacao = primary_order.get("variation", "") or order_info.get("variation", "") if order_info else ""
        sku = primary_order.get("sku", "") or order_info.get("sku", "") if order_info else ""
        
        # Dados do status e pagamento
        order_status = primary_order.get("status", "") or order_info.get("status", "") if order_info else ""
        payment_amount = primary_order.get("paymentAmount", "") or order_info.get("paymentAmount", "") if order_info else ""
        payment_currency = primary_order.get("paymentCurrency", "") or order_info.get("paymentCurrency", "") if order_info else ""
        payment_method = primary_order.get("paymentMethod", "") or order_info.get("paymentMethod", "") if order_info else ""
        payment_time = primary_order.get("paymentTime", "") or order_info.get("paymentTime", "") if order_info else ""
        total_orders = order_info.get("totalOrders", 1) if order_info else 1
        
        # Converte tipo de reclamação para português
        complaint_type_pt = {
            'falta_peca': 'Falta de Peça',
            'quebra': 'Quebra/Defeito',
            'outro': 'Outro'
        }.get(complaint.type, complaint.type)
        
        # Prepara mensagens e palavras-chave
        relevant_messages = " | ".join(msg.strip().replace("\n", " ") for msg in complaint.messages)
        keywords_str = ", ".join(complaint.keywords_found)
        
        # Linha para CSV com todas as colunas organizadas
        row = [
            timestamp,                    # timestamp_utc
            complaint.detected_at,        # detected_at
            order_id,                     # order_id
            buyer_name,                   # buyer_name
            produto,                      # produto
            variacao,                     # variacao
            sku,                          # sku
            order_status,                 # order_status
            payment_amount,               # payment_amount
            payment_currency,             # payment_currency
            payment_method,               # payment_method
            payment_time,                 # payment_time
            total_orders,                 # total_orders
            complaint_type_pt,            # complaint_type
            f"{complaint.confidence:.2f}", # confidence
            keywords_str,                 # keywords_found
            relevant_messages,            # relevant_messages
            "Novo",                       # status
            "Sim" if complaint.confidence >= 0.5 else "Não",  # marked_for_review
            ""                            # notes
        ]
        rows_to_save.append(row)
        
        # Entrada para JSON (mais detalhada)
        json_entry = {
            "timestamp_utc": timestamp,
            "complaint_data": asdict(complaint),
            "order_data": order_info,
            "processing_status": "novo",
            "flagged": complaint.confidence >= 0.5
        }
        json_entries.append(json_entry)
    
    # Salva no CSV
    with COMPLAINTS_CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in rows_to_save:
            writer.writerow(row)
    
    # Salva/atualiza JSON
    existing_data = []
    if COMPLAINTS_JSON_PATH.exists():
        try:
            with COMPLAINTS_JSON_PATH.open("r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            existing_data = []
    
    existing_data.extend(json_entries)
    
    with COMPLAINTS_JSON_PATH.open("w", encoding="utf-8") as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)
    
    # Também salva no Firestore usando a estrutura existente
    for row in rows_to_save:
        case_dict = {header: value for header, value in zip(COMPLAINTS_HEADER, row)}
        case_dict["resolvido"] = "false"
        case_dict["tipo"] = "reclamacao_detectada"
        save_case_document(case_dict)


def get_pending_complaints(min_confidence: float = 0.3) -> List[Dict[str, Any]]:
    """Retorna reclamações pendentes de análise"""
    if not COMPLAINTS_CSV_PATH.exists():
        return []
    
    pending = []
    with COMPLAINTS_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                confidence = float(row.get("confidence", "0"))
                status = row.get("status", "").lower()
                
                if confidence >= min_confidence and status in ["novo", "pendente", ""]:
                    pending.append(dict(row))
            except (ValueError, TypeError):
                continue
    
    return pending


def mark_complaint_reviewed(order_id: str, notes: str = "") -> bool:
    """Marca uma reclamação como revisada"""
    if not COMPLAINTS_CSV_PATH.exists():
        return False
    
    # Lê todas as linhas
    rows = []
    headers = []
    updated = False
    
    with COMPLAINTS_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader, [])
        for row in reader:
            if len(row) >= len(headers):
                row_dict = {headers[i]: row[i] for i in range(len(headers))}
                if row_dict.get("order_id") == order_id:
                    # Atualiza status e notas
                    if "status" in row_dict:
                        row_dict["status"] = "Revisado"
                    if "notes" in row_dict and notes:
                        row_dict["notes"] = notes
                    updated = True
                rows.append([row_dict.get(h, "") for h in headers])
    
    if updated:
        # Reescreve o arquivo
        with COMPLAINTS_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
    
    return updated


def get_complaints_summary() -> Dict[str, Any]:
    """Retorna resumo das reclamações detectadas"""
    if not COMPLAINTS_CSV_PATH.exists():
        return {
            "total": 0,
            "pending": 0,
            "high_confidence": 0,
            "by_type": {}
        }
    
    total = 0
    pending = 0
    high_confidence = 0
    by_type = {}
    
    with COMPLAINTS_CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            
            status = row.get("status", "").lower()
            if status in ["novo", "pendente", ""]:
                pending += 1
            
            try:
                confidence = float(row.get("confidence", "0"))
                if confidence >= 0.7:
                    high_confidence += 1
            except (ValueError, TypeError):
                pass
            
            complaint_type = row.get("complaint_type", "Desconhecido")
            by_type[complaint_type] = by_type.get(complaint_type, 0) + 1
    
    return {
        "total": total,
        "pending": pending,
        "high_confidence": high_confidence,
        "by_type": by_type
    }

