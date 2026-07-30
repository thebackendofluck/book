FROM golang:1.26-alpine AS builder

WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /cashout-pricing .

FROM alpine:3.24

RUN apk --no-cache add ca-certificates tzdata wget && \
    addgroup -S appgroup && adduser -S appuser -G appgroup
COPY --from=builder /cashout-pricing /usr/local/bin/cashout-pricing

RUN chown appuser:appgroup /usr/local/bin/cashout-pricing
USER appuser:appgroup

EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8085/health || exit 1

ENTRYPOINT ["cashout-pricing"]
