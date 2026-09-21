FROM golang:1.26-alpine AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o health-api .

FROM alpine:3.21

RUN apk add --no-cache ca-certificates tzdata wget

WORKDIR /app
COPY --from=builder /app/health-api .

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=30s --retries=6 \
    CMD wget -q -O /dev/null http://localhost:8080/health || exit 1

ENTRYPOINT ["./health-api"]
