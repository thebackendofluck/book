FROM golang:1.26-alpine AS builder

WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download

COPY *.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -o /bet-builder .

FROM alpine:3.24
RUN apk --no-cache add ca-certificates tzdata wget && \
    addgroup -S appgroup && adduser -S appuser -G appgroup
COPY --from=builder /bet-builder /bet-builder

RUN chown appuser:appgroup /bet-builder
USER appuser:appgroup

EXPOSE 8086

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8086/health || exit 1

ENTRYPOINT ["/bet-builder"]
