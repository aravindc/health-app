FROM docker.io/library/node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL
ENV VITE_API_URL=${VITE_API_URL}
# Commit the image was built from, exposed as <meta name="app-version">.
ARG GIT_SHA=unknown
ENV VITE_GIT_SHA=${GIT_SHA}
RUN npm run build

# nginx's own unprivileged image: runs as the `nginx` user (UID 101), with
# its PID and temp files in writable locations. It still listens on port 80
# (nginx.conf), which docker-compose allows non-root processes to bind via
# net.ipv4.ip_unprivileged_port_start, so Caddy's upstream health-fe:80 and
# the host port mapping are unchanged.
FROM docker.io/nginxinc/nginx-unprivileged:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Outside conf.d, which nginx includes wholesale at the http level.
COPY security-headers.conf /etc/nginx/security-headers.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
