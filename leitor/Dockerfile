# Leitor de documentos (ARQ1): sem rede para fora, sem credenciais, um documento por vez.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /leitor
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY leitor.py .
RUN useradd --system --uid 10002 --no-create-home leitor
USER leitor
EXPOSE 8090
CMD ["python", "leitor.py"]
