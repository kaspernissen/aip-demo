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

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/out /usr/share/nginx/html
EXPOSE 8080
