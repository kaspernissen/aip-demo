FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bootstrap.py correlation.py settlement.py mcp_tools.py executor.py main.py agent-card.json ./
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 9002
CMD ["python", "main.py"]
