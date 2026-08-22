FROM python:3.12-slim

WORKDIR /app

# Copia dependencias primero (mejor cacheo de capas)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia el código
COPY bot.py .

# Crea ficheros de datos por defecto (se sobrescriben con bind mount)
RUN touch /app/domains /app/authorized_users /app/invite_codes

# No correr como root (buena práctica)
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

CMD ["python", "bot.py"]
