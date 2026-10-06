FROM docker.io/library/golang:1.27-alpine AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o health-api .

FROM docker.io/library/alpine:3.21

RUN apk add --no-cache ca-certificates tzdata wget \
 && addgroup -S app && adduser -S -G app -H -h /app app

WORKDIR /app
COPY --from=builder /app/health-api .

EXPOSE 8080

# The healthcheck lives in docker-compose.yml, not here: Podman builds OCI
# images by default, and the OCI format has no HEALTHCHECK field, so a
# Dockerfile HEALTHCHECK would be silently dropped under Podman. wget is
# kept installed for that compose healthcheck.

# Run as an unprivileged user. health-api only reads its config and
# listens on ports above 1024 (8080, metrics 9090), so it needs no root.
USER app

ENTRYPOINT ["./health-api"]
