FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# SEG09: processo sem privilégios de root
RUN useradd --system --uid 10001 --no-create-home gateway && chown -R gateway /app
USER gateway
# 1 worker: o agendador de debounce roda dentro do processo
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-date-header"]
