FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scorer/ ./scorer/
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8300
CMD ["python", "-m", "scorer.server"]
