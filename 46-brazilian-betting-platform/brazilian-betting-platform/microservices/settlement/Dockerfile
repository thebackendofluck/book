# syntax=docker/dockerfile:1

FROM golang:1.26-alpine AS builder
RUN apk add --no-cache git

WORKDIR /src
COPY go.mod go.sum ./
ENV GONOSUMCHECK=* GONOSUMDB=* GOFLAGS="-mod=mod"
RUN go mod download || go mod tidy
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath -ldflags="-s -w" -o /settlement .

FROM alpine:3.24

LABEL org.opencontainers.image.title="settlement"
LABEL org.opencontainers.image.description="Brazilian betting platform — settlement engine microservice"

COPY --from=builder /settlement /settlement

RUN apk --no-cache add ca-certificates tzdata wget && \
    addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser:appgroup
EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:8082/health || exit 1

ENTRYPOINT ["/settlement"]
