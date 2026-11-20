# CardioSense console: static build served by nginx, which also proxies /v1 (REST + WebSocket) to the backend.
FROM node:22.23-slim AS build
RUN mkdir /src && chown node:node /src
USER node
WORKDIR /src
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci
COPY --chown=node:node . .
RUN npm run build

# shortcut: nginx master still runs as root (workers are unprivileged); nginxinc/nginx-unprivileged needs
# port 8080, so the compose `expose` and the Dokploy domain port must change together.
FROM nginx:1.28-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 80
