# -- Stage 1: Build --
FROM golang:1.26-alpine AS builder
RUN apk add --no-cache git

WORKDIR /build

COPY go.mod go.sum ./
ENV GONOSUMCHECK=* GONOSUMDB=* GOFLAGS="-mod=mod"
RUN go mod download || go mod tidy

COPY *.go ./
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -ldflags="-w -s -X main.version=1.0.0" -o casino-aggregation .

# -- Stage 2: Runtime --
FROM alpine:3.24

RUN apk --no-cache add ca-certificates tzdata wget && \
    addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app
COPY --from=builder /build/casino-aggregation .

ENV PORT=8040
ENV OPERATOR_CNPJ=00.000.000/0001-00
ENV LOG_PRETTY=false

USER appuser:appgroup
EXPOSE 8040

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8040/health || exit 1

ENTRYPOINT ["/app/casino-aggregation"]
