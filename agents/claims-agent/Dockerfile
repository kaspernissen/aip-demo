FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bootstrap.py correlation.py intake.py extraction.py svid.py model_client.py delegation.py executor.py ledger.py push.py main.py agent-card.json ./
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 9001
CMD ["python", "main.py"]
