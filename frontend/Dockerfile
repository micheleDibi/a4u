# syntax=docker/dockerfile:1.7

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install

FROM node:20-alpine AS build
WORKDIR /app
ARG VITE_API_BASE_URL=/api/v1
ARG VITE_UPLOADS_BASE_URL=/uploads
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_UPLOADS_BASE_URL=$VITE_UPLOADS_BASE_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime
# Stessa variabile dello stage di build: decide da dove il frontend carica
# le immagini (`src/lib/media.ts`) e quindi che cosa deve ammettere
# l'`img-src` della Content-Security-Policy. Con il default `/uploads` gli
# upload stanno sulla stessa origine e `'self'` basta; in produzione la
# variabile è un URL assoluto sullo storage OVH e va aggiunta la sua
# origine, altrimenti le immagini caricate spariscono.
ARG VITE_UPLOADS_BASE_URL=/uploads
COPY nginx.conf /etc/nginx/a4u-nginx.conf.in
# Il blocco è eseguito TALE E QUALE anche da
# `backend/tests/test_frontend_csp_header.py`, che lo estrae da qui e ne
# verifica l'esito nei due casi: perciò niente `sed -i` (BSD e GNU non
# concordano) e percorsi su una riga sola, come li cerca il test.
RUN set -eu; \
    case "$VITE_UPLOADS_BASE_URL" in \
      http://*|https://*) origin=" $(printf '%s' "$VITE_UPLOADS_BASE_URL" | cut -d/ -f1-3)" ;; \
      //*) origin=" $(printf '%s' "$VITE_UPLOADS_BASE_URL" | cut -d/ -f3)" ;; \
      *) origin="" ;; \
    esac; \
    sed "s|__A4U_UPLOADS_ORIGIN__|${origin}|" /etc/nginx/a4u-nginx.conf.in > /etc/nginx/conf.d/default.conf; \
    rm /etc/nginx/a4u-nginx.conf.in; \
    ! grep -q __A4U_UPLOADS_ORIGIN__ /etc/nginx/conf.d/default.conf; \
    grep -F 'add_header Content-Security-Policy' /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -qO- http://localhost/ >/dev/null || exit 1
