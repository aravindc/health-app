FROM docker.io/library/golang:1.27-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o health-mongo-sync .

FROM docker.io/library/alpine:latest
RUN apk --no-cache add ca-certificates tzdata \
 && addgroup -S app && adduser -S -G app -H -h /app app
WORKDIR /app
COPY --from=builder /app/health-mongo-sync .
# Run as an unprivileged user: it only reads its config, talks to the
# database and remote APIs, and writes no files.
USER app

CMD ["./health-mongo-sync"]
