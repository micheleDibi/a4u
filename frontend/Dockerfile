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
# verifica l'esito sui valori reali e su quelli malformati: perciò niente
# `sed -i` (BSD e GNU non concordano) e percorsi su una riga sola, come li
# cerca il test.
#
# Il valore è NORMALIZZATO prima di ricavarne l'origine e la riga generata
# è verificata nella FORMA, non solo nella presenza (Fase D, giro 8):
#   * gli spazi ai bordi sono tolti — un `.env` copiato male dava
#     ` https://host/u`, che il `case` non riconosceva come assoluto; il
#     browser invece li ignora nell'attributo e caricava da quell'host,
#     quindi la politica e `media.ts` divergevano e le immagini caricate
#     sparivano in silenzio;
#   * schema e host passano a minuscolo — `HTTPS://…` cadeva nel ramo `*)`
#     e l'origine spariva, mentre `media.ts` (`/^(https?:)?\/\//i`) lo
#     tratta come assoluto; per la CSP schema e host sono comunque
#     insensibili alle maiuscole, e il path non entra nell'origine;
#   * la `grep -E` finale pretende `img-src 'self' data: blob:` seguito al
#     più da un'origine ben formata, così un apice doppio (che iniettava
#     una direttiva nginx nel file generato), uno spazio interno o un path
#     residuo FANNO FALLIRE il build invece di passare inosservati. Anche
#     un host che esce dalla classe `[a-z0-9.-]` — un trattino basso
#     (`my_host.internal`), un IPv6 fra parentesi quadre — ferma il build:
#     la grammatica `host-source` della CSP non li ammette, quindi la
#     direttiva sarebbe scartata dal browser e le immagini caricate
#     sparirebbero in silenzio. Meglio fallire qui, a voce alta: in quei
#     casi l'origine va scritta a mano in `nginx.conf`.
RUN set -eu; \
    valore="$(printf '%s' "$VITE_UPLOADS_BASE_URL" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | tr 'A-Z' 'a-z')"; \
    case "$valore" in \
      http://*|https://*) origin=" $(printf '%s' "$valore" | cut -d/ -f1-3)" ;; \
      //*) origin=" $(printf '%s' "$valore" | cut -d/ -f3)" ;; \
      *) origin="" ;; \
    esac; \
    sed "s|__A4U_UPLOADS_ORIGIN__|${origin}|" /etc/nginx/a4u-nginx.conf.in > /etc/nginx/conf.d/default.conf; \
    rm /etc/nginx/a4u-nginx.conf.in; \
    ! grep -q __A4U_UPLOADS_ORIGIN__ /etc/nginx/conf.d/default.conf; \
    grep -Eq "^ *add_header Content-Security-Policy \"img-src 'self' data: blob:( https?://[a-z0-9.-]+(:[0-9]+)?| [a-z0-9.-]+(:[0-9]+)?)?\" always;$" /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -qO- http://localhost/ >/dev/null || exit 1
