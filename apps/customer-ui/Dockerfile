FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_CLAIMS_AGENT_URL=http://localhost:19001
ARG NEXT_PUBLIC_OTLP_ENDPOINT=http://localhost:4318
ENV NEXT_PUBLIC_CLAIMS_AGENT_URL=$NEXT_PUBLIC_CLAIMS_AGENT_URL
ENV NEXT_PUBLIC_OTLP_ENDPOINT=$NEXT_PUBLIC_OTLP_ENDPOINT
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server/bootstrap.py server/relay.py server/main.py ./
COPY --from=build /app/out /app/static
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8080
CMD ["python", "main.py"]
