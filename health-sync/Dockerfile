FROM docker.io/library/golang:1.27-alpine AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o health-sync .

FROM docker.io/library/alpine:3.21

RUN apk add --no-cache ca-certificates tzdata \
 && addgroup -S app && adduser -S -G app -H -h /app app

WORKDIR /app
COPY --from=builder /app/health-sync .

EXPOSE 8080

# Run as an unprivileged user: it only reads its config, talks to the
# database and remote APIs, and writes no files.
USER app

ENTRYPOINT ["./health-sync"]
