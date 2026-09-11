# 2026-09-11 (M3): was `golang:1.24-alpine`. otel-go v1.46.0 declares
# `go 1.25.0`, so 1.24 can no longer build this module. Pinned to the exact
# patch rather than the `1.27-alpine` floating tag, and deliberately the same
# release as `go.mod`'s `toolchain` line, so the builder never has to fetch a
# second toolchain. go1.27.1 was the current Go stable on that date (go.dev/dl).
FROM golang:1.27.1-alpine AS build
WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /out/payments-api .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/payments-api /payments-api
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/payments-api"]
