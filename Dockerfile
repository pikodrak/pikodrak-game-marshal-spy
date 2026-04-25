FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure runtime directories exist inside the image
RUN mkdir -p game_logs

EXPOSE 8030

CMD ["python", "server.py"]
