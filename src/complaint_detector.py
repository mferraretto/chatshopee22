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
        # Padrões para falta de peças/partes - LISTA COMPLETA FORNECIDA PELO USUÁRIO
        self.missing_parts_patterns = [
            # Palavras básicas de falta
            r"\bfaltando\b",
            r"\bfaltou\b", 
            r"\bestá faltando\b",
            r"\bcontinua faltando\b",
            r"\bficou faltando\b",
            r"\bainda falta\b",
            
            # Não recebimento
            r"\bnão veio\b",
            r"\bnão veio junto\b", 
            r"\bnão foi enviado\b",
            r"\bnão recebi\b",
            r"\bnão recebi tudo\b",
            r"\bnão chegou\b",
            r"\bnão chegou completo\b",
            r"\bnão mandaram\b",
            r"\bnão enviaram\b",
            r"\bnão entregaram\b",
            
            # Veio incompleto/errado
            r"\bveio sem\b",
            r"\bveio incompleto\b",
            r"\bveio errado\b",
            r"\bveio faltando\b",
            r"\bveio pela metade\b",
            r"\bveio quebrado\b",  # muitas vezes usam "quebrado" quando falta peça importante
            
            # Produto/pedido incompleto
            r"\bpedido incompleto\b",
            r"\bproduto incompleto\b",
            r"\bpeça não enviada\b",
            r"\bpeça não entregue\b",
            r"\bpeça faltante\b",
            r"\bsem a peça\b",
            r"\bincompleto\b",
            r"\bincompleta\b", 
            r"\bincompletos\b",
            
            # Erros de escrita comuns (clientes digitam rápido/irritados)
            r"\bfalatando\b",
            r"\bfalatou\b",
            r"\bn veio\b",
            r"\bnaum veio\b",
            r"\bn recebi\b",
            r"\bñ veio\b",
            r"\bñ recebi\b",
            r"\bencompleto\b",
            r"\bincopmleto\b",
            r"\bfaltao\b",
            r"\bfalto\b",
            
            # Expressões compostas
            r"\bveio errado e faltando\b",
            r"\bproduto veio faltando peça\b",
            r"\bnão mandaram tudo\b",
            r"\bnão enviaram completo\b",
            r"\bnão está completo\b",
            r"\bestá incompleto\b",
            r"\bveio sem a peça\b",
            r"\bveio faltando parte do produto\b",
            r"\bsó veio metade\b",
            
            # Padrões originais mantidos para compatibilidade
            r"\b(?:falta|faltou|faltando|não veio|nao veio|não chegou|nao chegou)\b.*\b(?:peça|peca|peças|pecas|parte|partes|item|itens)\b",
            r"\b(?:veio|chegou)\b.*\b(?:sem|faltando|menos)\b.*\b(?:peça|peca|peças|pecas|parte|partes)\b",
            r"\b(?:peça|peca|peças|pecas|parte|partes)\b.*\b(?:falta|faltou|faltando|não veio|nao veio)\b",
            r"\b(?:só|so|apenas|somente)\b.*\b(?:veio|chegou)\b",
            r"\b(?:cadê|onde está|onde esta|sumiu)\b.*\b(?:peça|peca|parte|item)\b",
            r"\b(?:esqueceram|esqueceu)\b.*\b(?:de mandar|de enviar|enviar)\b",
            r"\b(?:não mandaram|nao mandaram|não enviaram|nao enviaram)\b.*\b(?:tudo|completo|todas as peças|todas as pecas)\b"
        ]
        
        # Padrões para quebras/defeitos - LISTA COMPLETA FORNECIDA PELO USUÁRIO
        self.broken_patterns = [
            # Quebras básicas
            r"\bquebrado\b", r"\bquebrada\b", r"\bquebrou\b", 
            r"\bveio quebrado\b", r"\btodo quebrado\b", r"\bpartido\b", 
            r"\bestourado\b", r"\bcom trinca\b", r"\bcom rachadura\b",
            r"\bdanificado\b", r"\bavariado\b", 
            r"\bdanificou no transporte\b", r"\bquebrou na entrega\b",
            r"\bquebrou na base\b", r"\bquebrou no canto\b", r"\bquebrou o encaixe\b",
            
            # Variações sem acento/abreviações  
            r"\besta quebrado\b", r"\btá quebrado\b", r"\bta quebrado\b",
            
            # "chegou solto"
            r"\bchegou solto\b", r"\bsolto\b", r"\bveio solto\b", 
            r"\bpeça solta\b", r"\bbambo\b", r"\bbamba\b", r"\bfrouxo\b",
            r"\bdesencaixado\b", r"\bdesparafusado\b", r"\bcom folga\b",
            r"\bencaixe frouxo\b", r"\bparafuso solto\b", r"\bcola soltou\b",
            r"\bdescolou\b", r"\bcaiu do encaixe\b",
            
            # Modelo por partes: [PARTE] tá/está/veio [DANO]
            # Partes comuns
            r"\b(?:fundo|base|tampa|lateral|canto|borda|pé|suporte|haste|pino|parafuso|dobradiça|trilho|encaixe|topo|meio|coluna|arco|cilindro|painel)\b.*\b(?:quebrado|rachado|trincado|lascado|amassado|riscado|arranhado|descolado|solto|empenado|torto|desalinhado)\b",
            r"\b(?:quebrado|rachado|trincado|lascado|amassado|riscado|arranhado|descolado|solto|empenado|torto|desalinhado)\b.*\b(?:fundo|base|tampa|lateral|canto|borda|pé|suporte|haste|pino|parafuso|dobradiça|trilho|encaixe|topo|meio|coluna|arco|cilindro|painel)\b",
            
            # Exemplos específicos
            r"\bfundo tá quebrado\b", r"\bbase veio trincada\b", r"\bcanto amassado\b",
            r"\bencaixe solto\b", r"\bpé torto\b",
            
            # "tá com defeito"
            r"\bcom defeito\b", r"\bdefeituoso\b", r"\bdefeituosa\b",
            r"\bnão funciona\b", r"\bnão liga\b", r"\bnão fecha\b", r"\bnão abre\b",
            r"\bemperra\b", r"\btravando\b", r"\bnão encaixa\b", r"\bnão para em pé\b",
            r"\btorto\b", r"\bempenado\b", r"\bfora de prumo\b", r"\bdesalinhado\b",
            r"\bta com defeito\b", r"\besta com defeito\b",
            
            # "está rachado"
            r"\brachado\b", r"\brachada\b", r"\brachou\b", 
            r"\btrincado\b", r"\btrincada\b", r"\btrincou\b",
            r"\bfissurado\b", r"\bfissura\b", r"\bcom trinca\b", r"\bcom rachadura\b",
            r"\babriu uma trinca\b", r"\besta rachado\b", r"\bta rachado\b",
            
            # Extras úteis (dano/estética/embalagem)
            r"\bamassado\b", r"\bamassou\b", r"\besmagado\b", r"\briscado\b",
            r"\barranhado\b", r"\blascado\b", r"\bdescascando\b", r"\bpintura falhada\b",
            r"\bmanchado\b", r"\bsujo\b", r"\bmolhado\b", r"\bmofado\b",
            r"\bembalagem amassada\b", r"\bcaixa amassada\b", 
            r"\bchegou avariado\b", r"\bchegou danificado\b",
            
            # Erros de digitação comuns
            r"\bquebrda\b", r"\brachdo\b", r"\btricando\b", r"\bsouto\b", r"\bsoltoo\b",
            r"\bdefeio\b", r"\bdefeitoo\b", r"\bempeno\b", r"\bempenada\b",
            
            # Padrões originais mantidos para compatibilidade
            r"\b(?:defeituoso|defeituosa|com defeito|defeito)\b",
            r"\b(?:danificado|danificada|danificar|avariado|avariada)\b",
            r"\b(?:não funciona|nao funciona|não está funcionando|nao esta funcionando)\b",
            r"\b(?:parou de funcionar|deixou de funcionar)\b",
            r"\b(?:ruim|péssimo|pessimo|horrível|horrivel)\b.*\b(?:qualidade|material)\b",
            r"\b(?:veio|chegou)\b.*\b(?:quebrado|quebrada|defeituoso|defeituosa|danificado|danificada)\b"
        ]
        
        # Palavras-chave adicionais para contexto
        self.context_keywords = {
            'falta_peca': ['parafuso', 'parafusos', 'porca', 'porcas', 'peça', 'pecas', 'parte', 'partes', 'item', 'itens', 'componente', 'componentes'],
            'quebra': ['quebrou', 'quebrado', 'quebrada', 'defeito', 'defeituoso', 'danificado', 'rachado', 'trincado'],
            'urgencia': ['urgente', 'rápido', 'rapido', 'logo', 'hoje', 'amanha', 'amanhã', 'preciso', 'precisa']
        }
    
    def detect_missing_parts(self, messages: List[str]) -> bool:
        """Detecta se há reclamação de falta de peças"""
        if not messages:
            return False
            
        combined_text = ' '.join(messages[-10:]).lower()  # Aumentado para últimas 10 mensagens
        print(f"[DEBUG] 🔍 Verificando falta de peças em: '{combined_text[:200]}...'")
        
        for i, pattern in enumerate(self.missing_parts_patterns):
            if re.search(pattern, combined_text, re.IGNORECASE):
                print(f"[DEBUG] ✅ FALTA DE PEÇA DETECTADA! Padrão {i+1}: {pattern}")
                return True
        
        print("[DEBUG] ❌ Nenhuma falta de peça detectada")
        return False
    
    def detect_breakage(self, messages: List[str]) -> bool:
        """Detecta se há reclamação de quebra/defeito"""
        if not messages:
            return False
            
        combined_text = ' '.join(messages[-10:]).lower()  # Aumentado para últimas 10 mensagens
        print(f"[DEBUG] 🔍 Verificando quebras/defeitos em: '{combined_text[:200]}...'")
        
        for i, pattern in enumerate(self.broken_patterns):
            if re.search(pattern, combined_text, re.IGNORECASE):
                print(f"[DEBUG] ✅ QUEBRA/DEFEITO DETECTADO! Padrão {i+1}: {pattern}")
                return True
        
        print("[DEBUG] ❌ Nenhuma quebra/defeito detectado")
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
        combined_text = ' '.join(messages[-10:]).lower()  # Mais mensagens para análise
        confidence = 0.0
        
        # Padrões aplicáveis
        patterns = []
        if complaint_type == 'falta_peca':
            patterns = self.missing_parts_patterns
        elif complaint_type == 'quebra':
            patterns = self.broken_patterns
        
        print(f"[DEBUG] 📊 Calculando confiança para '{complaint_type}' com {len(patterns)} padrões")
        
        # Conta quantos padrões foram encontrados
        matches = 0
        matched_patterns = []
        for pattern in patterns:
            if re.search(pattern, combined_text, re.IGNORECASE):
                matches += 1
                matched_patterns.append(pattern)
        
        print(f"[DEBUG] 📊 Padrões encontrados: {matches}/{len(patterns)}")
        
        # Confidence baseada na proporção de padrões encontrados (mais generoso)
        if matches > 0:
            # Confiança mínima de 0.5 se encontrou pelo menos um padrão
            confidence = max(0.5, min(1.0, matches / len(patterns) * 5))  # Mais generoso
        
        # Bonus se há palavras de urgência
        urgency_words = self.context_keywords.get('urgencia', [])
        urgency_found = 0
        for word in urgency_words:
            if word in combined_text:
                confidence += 0.1
                urgency_found += 1
        
        final_confidence = min(1.0, confidence)
        print(f"[DEBUG] 📊 Confiança final: {final_confidence:.2f} (matches={matches}, urgência={urgency_found})")
        
        return final_confidence
    
    def analyze_messages(self, messages: List[str], order_info: Dict[str, Any] = None) -> List[ComplaintInfo]:
        """Analisa mensagens e retorna lista de reclamações detectadas"""
        complaints = []
        
        if not messages:
            print("[DEBUG] ❌ Nenhuma mensagem para analisar")
            return complaints
        
        print(f"[DEBUG] 🔍 Analisando {len(messages)} mensagens para reclamações...")
        
        # Detecta falta de peças
        if self.detect_missing_parts(messages):
            confidence = self.calculate_confidence(messages, 'falta_peca')
            keywords = self.get_found_keywords(messages, 'falta_peca')
            
            complaint = ComplaintInfo(
                type='falta_peca',
                confidence=confidence,
                messages=messages[-10:],  # Mais mensagens para contexto
                keywords_found=keywords,
                order_info=order_info
            )
            complaints.append(complaint)
            print(f"[DEBUG] ✅ Reclamação FALTA DE PEÇA adicionada (confiança: {confidence:.2f})")
        
        # Detecta quebras/defeitos
        if self.detect_breakage(messages):
            confidence = self.calculate_confidence(messages, 'quebra')
            keywords = self.get_found_keywords(messages, 'quebra')
            
            complaint = ComplaintInfo(
                type='quebra',
                confidence=confidence,
                messages=messages[-10:],  # Mais mensagens para contexto
                keywords_found=keywords,
                order_info=order_info
            )
            complaints.append(complaint)
            print(f"[DEBUG] ✅ Reclamação QUEBRA/DEFEITO adicionada (confiança: {confidence:.2f})")
        
        print(f"[DEBUG] 📊 Total de reclamações detectadas: {len(complaints)}")
        return complaints
    
    def should_flag_conversation(self, messages: List[str], min_confidence: float = 0.3) -> bool:
        """Determina se a conversa deve ser marcada para atenção manual
        
        Retorna True apenas para problemas específicos:
        - Falta de peças/partes 
        - Quebras/defeitos
        
        Ignora conversas normais (dúvidas, elogios, perguntas sobre entrega, etc.)
        """
        if not messages:
            return False
            
        complaints = self.analyze_messages(messages)
        
        # Verifica se há reclamações específicas com confiança suficiente
        for complaint in complaints:
            if complaint.confidence >= min_confidence:
                # Só marca problemas específicos que requerem ação
                if complaint.type in ['falta_peca', 'quebra']:
                    return True
        
        return False
    
    def is_normal_conversation(self, messages: List[str]) -> bool:
        """Verifica se é uma conversa normal (sem problemas específicos)
        
        IMPORTANTE: Primeiro verifica se há problemas específicos.
        Se houver, NÃO classifica como normal mesmo que tenha padrões normais.
        """
        if not messages:
            return True
            
        combined_text = ' '.join(messages[-10:]).lower()  # Mais mensagens para análise
        print(f"[DEBUG] 🔍 Verificando se é conversa normal: '{combined_text[:150]}...'")
        
        # PRIMEIRO: Verifica se há problemas específicos (prioridade alta)
        has_missing_parts = self.detect_missing_parts(messages)
        has_breakage = self.detect_breakage(messages)
        
        if has_missing_parts or has_breakage:
            print(f"[DEBUG] 🚨 NÃO É NORMAL - Problemas detectados: falta_peca={has_missing_parts}, quebra={has_breakage}")
            return False
        
        # SEGUNDO: Se não há problemas, verifica padrões normais
        normal_patterns = [
            r'\b(quando|como|onde)\b.*\b(chega|chegará|vai chegar|entrega)\b',
            r'\b(obrigad[oa]|agradec)\b',
            r'\b(qual|como)\b.*\b(usar|utilizar|montar|instalar)\b',
            r'\b(muito bom|excelente|ótimo|otimo|perfeito)\b',
            r'\b(dúvida|duvida|pergunta)\b.*\b(sobre|do|da)\b',
            r'\b(prazo|tempo)\b.*\b(entrega|envio)\b',
            r'\b(rastreamento|código|tracking)\b',
        ]
        
        for pattern in normal_patterns:
            if re.search(pattern, combined_text, re.IGNORECASE):
                print(f"[DEBUG] ✅ Conversa normal detectada - padrão: {pattern}")
                return True
                
        print("[DEBUG] ❓ Conversa não classificada como normal - prosseguindo com análise")        
        return False


def create_detector() -> ComplaintDetector:
    """Factory function para criar o detector"""
    return ComplaintDetector()
