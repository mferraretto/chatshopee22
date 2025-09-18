# Usa a imagem oficial do Playwright com browsers já instalados
FROM mcr.microsoft.com/playwright/python:v1.46.0-jammy

# Impede Python de gerar .pyc e usa stdout sem buffer
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Playwright/Chromium flags para Cloud Run (sem sandbox, sem GPU)
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYPPETEER_EXECUTABLE_PATH=/ms-playwright/chromium-1124/chrome-linux/chrome \
    # Configurações do Cloud Run
    PORT=8080 \
    HOST=0.0.0.0

WORKDIR /app

# Só copie os arquivos que mudam pouco para cachear melhor
COPY requirements.txt /app/

# Instala as libs Python do seu projeto
RUN pip install --no-cache-dir -r requirements.txt

# Instala fontes comuns para evitar bloqueios de carregamento
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-liberation fonts-noto-color-emoji fonts-dejavu-core \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Cria diretórios necessários
RUN mkdir -p /app/data /app/sessions /app/pw-user-data

# Copia o resto do código
COPY . /app

# Define permissões adequadas
RUN chmod -R 755 /app

# Health check para Cloud Run
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/healthz || exit 1

# Comando de start com timeout aumentado e logs detalhados
CMD ["sh", "-c", "echo 'Iniciando aplicação...' && uvicorn app_ui:app --host ${HOST} --port ${PORT} --timeout-keep-alive 120 --log-level info"]
