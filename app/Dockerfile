FROM python:3.12-slim

WORKDIR /app

# (Optional) small QoL: avoid .pyc, unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

EXPOSE 8080
CMD ["python", "app.py"]
