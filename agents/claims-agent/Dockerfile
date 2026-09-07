FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# correlation.py is added here in Task 7 (A6) — omitted for now, its absence
# would fail this build.
COPY bootstrap.py intake.py delegation.py executor.py main.py agent-card.json ./
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 9001
CMD ["python", "main.py"]
