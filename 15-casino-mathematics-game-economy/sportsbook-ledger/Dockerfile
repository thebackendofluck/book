FROM golang:1.26-alpine AS builder
WORKDIR /app
COPY go.mod go.sum* ./
RUN go mod download || true
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o ledger ./cmd/main.go

FROM alpine:3.24
RUN apk add --no-cache ca-certificates

# Create non-root user
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app
COPY --from=builder /app/ledger .

RUN chown appuser:appgroup /app/ledger

USER appuser:appgroup

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8080/health || exit 1

ENTRYPOINT ["./ledger"]
