# otel-go v1.46.0 declares `go 1.25.0`, so a 1.24 toolchain cannot build this
# module. Pinned to the exact patch rather than the `1.27-alpine` floating
# tag, and deliberately the same release as `go.mod`'s `toolchain` line, so
# the builder never has to fetch a second toolchain.
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
