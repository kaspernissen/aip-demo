FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# svid.py is imported by mcp_tools.py, so leaving it out here would fail at
# import time in-cluster while every local test still passed.
COPY bootstrap.py correlation.py settlement.py svid.py mcp_tools.py executor.py main.py agent-card.json ./
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 9002
CMD ["python", "main.py"]
