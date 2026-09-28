FROM docker.io/library/golang:1.27-alpine AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o health-sync .

FROM docker.io/library/alpine:3.21

RUN apk add --no-cache ca-certificates tzdata

WORKDIR /app
COPY --from=builder /app/health-sync .

EXPOSE 8080

ENTRYPOINT ["./health-sync"]
