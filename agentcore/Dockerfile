FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY main.py k8s_tools.py escalation_tools.py ./

EXPOSE 8080

CMD ["python", "main.py"]
