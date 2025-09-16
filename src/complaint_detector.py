# src/complaint_detector.py

import re
from typing import List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

@dataclass
class ComplaintInfo:
    """Informações sobre uma reclamação detectada"""
    type: str  # 'falta_peca', 'quebra', 'outro'
    confidence: float  # 0.0 a 1.0
    messages: List[str]  # mensagens que indicam o problema
    keywords_found: List[str]  # palavras-chave encontradas
    order_info: Dict[str, Any] = None
    detected_at: str = None
    
    def __post_init__(self):
        if self.detected_at is None:
            self.detected_at = datetime.now().isoformat()


class ComplaintDetector:
    """Detecta reclamações de falta de peças/quebras nas mensagens dos clientes"""
    
    def __init__(self):
        # Padrões para falta de peças/partes
        self.missing_parts_patterns = [
            r"\b(?:falta|faltou|faltando|não veio|nao veio|não chegou|nao chegou)\b.*\b(?:peça|peca|peças|pecas|parte|partes|item|itens)\b",
            r"\b(?:veio|chegou)\b.*\b(?:sem|faltando|menos)\b.*\b(?:peça|peca|peças|pecas|parte|partes)\b",
            r"\b(?:peça|peca|peças|pecas|parte|partes)\b.*\b(?:falta|faltou|faltando|não veio|nao veio)\b",
            r"\b(?:incompleto|incompleta|não completo|nao completo)\b",
            r"\b(?:só|so|apenas|somente)\b.*\b(?:veio|chegou)\b",
            r"\b(?:cadê|onde está|onde esta|sumiu)\b.*\b(?:peça|peca|parte|item)\b",
            r"\b(?:esqueceram|esqueceu)\b.*\b(?:de mandar|de enviar|enviar)\b",
            r"\b(?:não mandaram|nao mandaram|não enviaram|nao enviaram)\b.*\b(?:tudo|completo|todas as peças|todas as pecas)\b"
        ]
        
        # Padrões para quebras/defeitos
        self.broken_patterns = [
            r"\b(?:quebrado|quebrada|quebrou|quebrar|rachado|rachada|rachou)\b",
            r"\b(?:defeituoso|defeituosa|com defeito|defeito)\b",
            r"\b(?:danificado|danificada|danificar|avariado|avariada)\b",
            r"\b(?:não funciona|nao funciona|não está funcionando|nao esta funcionando)\b",
            r"\b(?:parou de funcionar|deixou de funcionar)\b",
            r"\b(?:ruim|péssimo|pessimo|horrível|horrivel)\b.*\b(?:qualidade|material)\b",
            r"\b(?:veio|chegou)\b.*\b(?:quebrado|quebrada|defeituoso|defeituosa|danificado|danificada)\b",
            r"\b(?:trincado|trincada|riscado|riscada|amassado|amassada)\b"
        ]
        
        # Palavras-chave adicionais para contexto
        self.context_keywords = {
            'falta_peca': ['parafuso', 'parafusos', 'porca', 'porcas', 'peça', 'pecas', 'parte', 'partes', 'item', 'itens', 'componente', 'componentes'],
            'quebra': ['quebrou', 'quebrado', 'quebrada', 'defeito', 'defeituoso', 'danificado', 'rachado', 'trincado'],
            'urgencia': ['urgente', 'rápido', 'rapido', 'logo', 'hoje', 'amanha', 'amanhã', 'preciso', 'precisa']
        }
    
    def detect_missing_parts(self, messages: List[str]) -> bool:
        """Detecta se há reclamação de falta de peças"""
        combined_text = ' '.join(messages[-5:]).lower()  # últimas 5 mensagens
        
        for pattern in self.missing_parts_patterns:
            if re.search(pattern, combined_text, re.IGNORECASE):
                return True
        return False
    
    def detect_breakage(self, messages: List[str]) -> bool:
        """Detecta se há reclamação de quebra/defeito"""
        combined_text = ' '.join(messages[-5:]).lower()  # últimas 5 mensagens
        
        for pattern in self.broken_patterns:
            if re.search(pattern, combined_text, re.IGNORECASE):
                return True
        return False
    
    def get_found_keywords(self, messages: List[str], complaint_type: str) -> List[str]:
        """Extrai palavras-chave encontradas para o tipo de reclamação"""
        combined_text = ' '.join(messages).lower()
        found = []
        
        if complaint_type in self.context_keywords:
            for keyword in self.context_keywords[complaint_type]:
                if keyword in combined_text:
                    found.append(keyword)
        
        return found
    
    def calculate_confidence(self, messages: List[str], complaint_type: str) -> float:
        """Calcula a confiança na detecção da reclamação"""
        combined_text = ' '.join(messages[-5:]).lower()
        confidence = 0.0
        
        # Padrões aplicáveis
        patterns = []
        if complaint_type == 'falta_peca':
            patterns = self.missing_parts_patterns
        elif complaint_type == 'quebra':
            patterns = self.broken_patterns
        
        # Conta quantos padrões foram encontrados
        matches = 0
        for pattern in patterns:
            if re.search(pattern, combined_text, re.IGNORECASE):
                matches += 1
        
        # Confidence baseada na proporção de padrões encontrados
        if matches > 0:
            confidence = min(1.0, matches / len(patterns) * 3)  # amplifica um pouco
        
        # Bonus se há palavras de urgência
        urgency_words = self.context_keywords.get('urgencia', [])
        for word in urgency_words:
            if word in combined_text:
                confidence += 0.1
        
        return min(1.0, confidence)
    
    def analyze_messages(self, messages: List[str], order_info: Dict[str, Any] = None) -> List[ComplaintInfo]:
        """Analisa mensagens e retorna lista de reclamações detectadas"""
        complaints = []
        
        if not messages:
            return complaints
        
        # Detecta falta de peças
        if self.detect_missing_parts(messages):
            confidence = self.calculate_confidence(messages, 'falta_peca')
            keywords = self.get_found_keywords(messages, 'falta_peca')
            
            complaint = ComplaintInfo(
                type='falta_peca',
                confidence=confidence,
                messages=messages[-5:],  # últimas 5 mensagens relevantes
                keywords_found=keywords,
                order_info=order_info
            )
            complaints.append(complaint)
        
        # Detecta quebras/defeitos
        if self.detect_breakage(messages):
            confidence = self.calculate_confidence(messages, 'quebra')
            keywords = self.get_found_keywords(messages, 'quebra')
            
            complaint = ComplaintInfo(
                type='quebra',
                confidence=confidence,
                messages=messages[-5:],  # últimas 5 mensagens relevantes
                keywords_found=keywords,
                order_info=order_info
            )
            complaints.append(complaint)
        
        return complaints
    
    def should_flag_conversation(self, messages: List[str], min_confidence: float = 0.3) -> bool:
        """Determina se a conversa deve ser marcada para atenção manual"""
        complaints = self.analyze_messages(messages)
        
        for complaint in complaints:
            if complaint.confidence >= min_confidence:
                return True
        
        return False


def create_detector() -> ComplaintDetector:
    """Factory function para criar o detector"""
    return ComplaintDetector()
