FROM python:3.10-slim

WORKDIR /app

# Garante que os logs apareçam imediatamente no `docker logs`
ENV PYTHONUNBUFFERED=1

# Instala as bibliotecas Python (psycopg2-binary já inclui o libpq, sem precisar de gcc)
RUN pip install --no-cache-dir "paho-mqtt>=2,<3" "psycopg2-binary>=2.9,<3"

# Copia o código para dentro do container
COPY main.py .

# Executa o script
CMD ["python", "main.py"]
