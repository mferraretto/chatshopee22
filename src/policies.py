from __future__ import annotations
from pathlib import Path
import re

POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"

POLICY_FILES = {
    "nao_altera_endereco": POLICY_DIR / "nao_altera_endereco.md",
    "nao_cobra_fora_app": POLICY_DIR / "nao_cobra_fora_app.md",
    "reembolso": POLICY_DIR / "reembolso.md",
    "tabela_medidas": POLICY_DIR / "tabela_medidas.md",
}

ENDERECO_RE = re.compile(r"\bendere[cç]o\b|\balterar endereco\b|\bmudar endereco\b", re.I)
OFF_APP_RE = re.compile(r"\b(pix|transfer[êe]ncia|dep[oó]sito|boleto|chave|whatsapp|zap)\b", re.I)
REEMBOLSO_RE = re.compile(r"\breembolso\b|\bdevolu[cç][aã]o\b|\bdevolver\b", re.I)
MEDIDAS_RE = re.compile(r"\bmedidas?\b|\btamanho\b|\btabela de medidas\b", re.I)


def detect_policies(text: str) -> list[str]:
    """Retorna lista de IDs de políticas com base no texto do cliente."""
    policies: list[str] = []
    if ENDERECO_RE.search(text):
        policies.append("nao_altera_endereco")
    if OFF_APP_RE.search(text):
        policies.append("nao_cobra_fora_app")
    if REEMBOLSO_RE.search(text):
        policies.append("reembolso")
    if MEDIDAS_RE.search(text):
        policies.append("tabela_medidas")
    return policies


def load_snippets(policy_ids: list[str]) -> str:
    """Lê os arquivos das políticas e monta o bloco de contexto."""
    snippets = []
    for pid in policy_ids:
        path = POLICY_FILES.get(pid)
        if path and path.exists():
            snippets.append(path.read_text(encoding="utf-8").strip())
    if snippets:
        return "[Políticas/Respostas Oficiais]\n" + "\n\n".join(snippets) + "\n\n"
    return ""
