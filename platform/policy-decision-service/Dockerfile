FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Ruling R22: the stubs are generated here, from the vendored proto, by the same
# checked-in script a developer runs. They are never COPYied in, so the image
# and the repo cannot disagree about a contract whose source of truth is
# proto/ext_mcp.proto. `python` is the image's own interpreter; the script
# defaults to the dev venv's.
COPY proto/ ./proto/
COPY gen-stubs.sh ./
RUN ./gen-stubs.sh python
COPY pds/ ./pds/
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 9100
CMD ["python", "-m", "pds.server"]
