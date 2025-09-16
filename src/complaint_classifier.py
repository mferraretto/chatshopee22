# src/complaint_classifier.py

"""
Novo classificador que detecta reclamações em vez de gerar respostas.
Este arquivo substitui a funcionalidade do classifier.py original.
"""

from __future__ import annotations
from typing import List, Tuple, Dict, Any
import re

from .complaint_detector import ComplaintDetector, create_detector
from .complaints_storage import save_complaints


class ComplaintClassifier:
    """Classifica conversas identificando reclamações de falta de peças/quebras"""
    
    def __init__(self):
        self.detector = create_detector()
        # Contador para debug
        self.processed_conversations = 0
        self.flagged_conversations = 0
    
    def analyze_conversation(
        self, 
        pairs: List[Tuple[str, str]], 
        buyer_only: List[str], 
        order_info: dict | None = None
    ) -> Tuple[bool, str]:
        """
        Analisa uma conversa e determina se deve ser marcada.
        
        Retorna:
            Tuple[bool, str]: (should_flag, reason)
            - should_flag: True se a conversa deve ser marcada para revisão
            - reason: Motivo da marcação ou mensagem de status
        """
        self.processed_conversations += 1
        
        if not buyer_only:
            return False, "Nenhuma mensagem do comprador encontrada"
        
        # Limita a análise às últimas 20 mensagens como solicitado
        recent_messages = buyer_only[-20:]
        
        # Analisa as mensagens em busca de reclamações
        complaints = self.detector.analyze_messages(recent_messages, order_info)
        
        if not complaints:
            return False, f"Nenhuma reclamação detectada (processadas {self.processed_conversations} conversas)"
        
        # Filtra apenas reclamações com confiança mínima
        significant_complaints = [c for c in complaints if c.confidence >= 0.3]
        
        if not significant_complaints:
            return False, f"Reclamações com baixa confiança ignoradas"
        
        # Salva as reclamações detectadas
        try:
            save_complaints(significant_complaints, order_info)
            self.flagged_conversations += 1
            
            # Cria resumo das reclamações detectadas
            complaint_types = []
            for complaint in significant_complaints:
                if complaint.type == 'falta_peca':
                    complaint_types.append(f"Falta de peça (conf: {complaint.confidence:.2f})")
                elif complaint.type == 'quebra':
                    complaint_types.append(f"Quebra/defeito (conf: {complaint.confidence:.2f})")
                else:
                    complaint_types.append(f"{complaint.type} (conf: {complaint.confidence:.2f})")
            
            reason = f"🚨 RECLAMAÇÃO DETECTADA: {', '.join(complaint_types)}"
            reason += f"\n💾 Salvo para revisão manual ({self.flagged_conversations}/{self.processed_conversations} conversas marcadas)"
            
            return True, reason
            
        except Exception as e:
            return True, f"⚠️ Reclamação detectada mas erro ao salvar: {e}"
    
    def get_statistics(self) -> Dict[str, Any]:
        """Retorna estatísticas do processamento"""
        return {
            "processed_conversations": self.processed_conversations,
            "flagged_conversations": self.flagged_conversations,
            "flag_rate": self.flagged_conversations / max(1, self.processed_conversations)
        }


# Instância global do classificador
_classifier_instance = None

def get_classifier() -> ComplaintClassifier:
    """Retorna a instância singleton do classificador"""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = ComplaintClassifier()
    return _classifier_instance


def decide_reply(
    pairs: List[Tuple[str, str]],
    buyer_only: List[str],
    order_info: dict | None = None,
) -> Tuple[bool, str]:
    """
    Função compatível com a interface original do classifier.py
    
    Agora detecta e marca reclamações em vez de gerar respostas.
    
    Retorna:
        Tuple[bool, str]: (False, status_message)
        - Sempre retorna False para should_reply (não responder mais)
        - status_message: Informações sobre o processamento
    """
    classifier = get_classifier()
    should_flag, reason = classifier.analyze_conversation(pairs, buyer_only, order_info)
    
    if should_flag:
        # Conversa foi marcada para revisão - não responder automaticamente
        return False, f"MARCADO: {reason}"
    else:
        # Conversa normal - também não responder (novo comportamento)
        return False, f"OK: {reason}"


# Função auxiliar para extrair informações adicionais das mensagens
def extract_complaint_context(messages: List[str]) -> Dict[str, Any]:
    """Extrai contexto adicional das mensagens para análise mais detalhada"""
    combined = " ".join(messages[-10:]).lower()  # últimas 10 mensagens
    
    context = {
        "urgency_indicators": [],
        "specific_items_mentioned": [],
        "satisfaction_level": "neutral"
    }
    
    # Indicadores de urgência
    urgency_patterns = [
        r"\b(urgente|rápido|rapido|logo|hoje|amanha|amanhã)\b",
        r"\b(preciso|precisa)\s+(urgente|rápido|logo)\b",
        r"\b(não posso esperar|nao posso esperar)\b"
    ]
    
    for pattern in urgency_patterns:
        matches = re.findall(pattern, combined, re.IGNORECASE)
        context["urgency_indicators"].extend(matches)
    
    # Itens específicos mencionados
    item_patterns = [
        r"\b(parafuso|parafusos)\b",
        r"\b(porca|porcas)\b", 
        r"\b(chave|chaves)\b",
        r"\b(manual|manuais)\b"
    ]
    
    for pattern in item_patterns:
        matches = re.findall(pattern, combined, re.IGNORECASE)
        context["specific_items_mentioned"].extend(matches)
    
    # Nível de satisfação
    negative_patterns = [
        r"\b(péssimo|pessimo|horrível|horrivel|ruim)\b",
        r"\b(decepcionado|decepcionada|frustrado|frustrada)\b",
        r"\b(não recomendo|nao recomendo)\b"
    ]
    
    positive_patterns = [
        r"\b(obrigado|obrigada|agradeço|agradeco)\b",
        r"\b(bom|boa|excelente|ótimo|otimo)\b"
    ]
    
    if any(re.search(pattern, combined) for pattern in negative_patterns):
        context["satisfaction_level"] = "negative"
    elif any(re.search(pattern, combined) for pattern in positive_patterns):
        context["satisfaction_level"] = "positive"
    
    return context
