#!/usr/bin/env python3
"""
Script de debug para testar a inicialização da aplicação
Útil para diagnosticar problemas no Cloud Run
"""

import os
import sys
import logging
from pathlib import Path

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def check_environment():
    """Verifica o ambiente de execução"""
    logger.info("🔍 Verificando ambiente...")
    
    # Informações do sistema
    logger.info(f"🐍 Python: {sys.version}")
    logger.info(f"📁 Working dir: {os.getcwd()}")
    logger.info(f"🌐 PORT: {os.getenv('PORT', 'não definida')}")
    logger.info(f"🏠 HOST: {os.getenv('HOST', 'não definida')}")
    
    # Verifica se os diretórios necessários existem
    directories = ["data", "sessions", "pw-user-data", "src"]
    for directory in directories:
        path = Path(directory)
        exists = path.exists()
        logger.info(f"📁 {directory}: {'✅ existe' if exists else '❌ não existe'}")
        
    # Lista arquivos importantes
    important_files = [
        "app_ui.py", "main.py", "requirements.txt", 
        "src/config.py", "src/duoke.py", "src/complaint_detector.py"
    ]
    for file in important_files:
        path = Path(file)
        exists = path.exists()
        logger.info(f"📄 {file}: {'✅ existe' if exists else '❌ não existe'}")

def test_imports():
    """Testa as importações principais"""
    logger.info("📦 Testando importações...")
    
    imports_to_test = [
        ("fastapi", "FastAPI"),
        ("uvicorn", None),
        ("jinja2", "Template"),
        ("playwright.async_api", "async_playwright"),
        ("openpyxl", "Workbook"),
        ("cryptography.hazmat.primitives.ciphers.aead", "AESGCM")
    ]
    
    for module, attr in imports_to_test:
        try:
            imported = __import__(module, fromlist=[attr] if attr else [])
            if attr:
                getattr(imported, attr)
            logger.info(f"✅ {module}: OK")
        except ImportError as e:
            logger.error(f"❌ {module}: {e}")
        except AttributeError as e:
            logger.error(f"❌ {module}.{attr}: {e}")

def test_app_startup():
    """Testa a inicialização da aplicação"""
    logger.info("🚀 Testando inicialização da aplicação...")
    
    try:
        # Tenta importar a aplicação
        from app_ui import app
        logger.info("✅ app_ui importado com sucesso")
        
        # Verifica se a aplicação foi criada
        if app:
            logger.info("✅ FastAPI app criada com sucesso")
        else:
            logger.error("❌ FastAPI app é None")
            
    except Exception as e:
        logger.error(f"❌ Erro ao importar app_ui: {e}")
        import traceback
        traceback.print_exc()

def main():
    """Função principal de debug"""
    logger.info("🔧 Iniciando debug da aplicação...")
    
    check_environment()
    test_imports()
    test_app_startup()
    
    logger.info("🎉 Debug concluído!")

if __name__ == "__main__":
    main()
