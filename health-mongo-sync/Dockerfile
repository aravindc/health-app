FROM docker.io/library/golang:1.27.2-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
# -trimpath and -s -w leave out local paths and debug symbols: a smaller
# binary, and stack traces still work.
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o health-mongo-sync .

FROM docker.io/library/alpine:3.21
RUN apk --no-cache add ca-certificates tzdata \
 && addgroup -S app && adduser -S -G app -H -h /app app
WORKDIR /app
COPY --from=builder /app/health-mongo-sync .
# Run as an unprivileged user: it only reads its config, talks to the
# database and remote APIs, and writes no files.
USER app

CMD ["./health-mongo-sync"]
