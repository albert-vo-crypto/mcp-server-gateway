# A corporate TLS-inspecting proxy (Zscaler, Netskope, ...) re-signs
# registry.npmjs.org with a private root. Node ships its own CA bundle and
# ignores the system trust store, so npm fails with
# UNABLE_TO_GET_ISSUER_CERT_LOCALLY unless that root is handed to it explicitly.
# The bind mount keeps certs/ out of the image layers — it also holds local dev
# private keys, which must not ship. No-op when certs/ has no *-ca.pem.
FROM node:24-alpine AS builder
WORKDIR /app

RUN --mount=type=bind,source=certs,target=/tmp/certs \
    cat /tmp/certs/*-ca.pem > /usr/local/share/corporate-ca.crt 2>/dev/null \
    || touch /usr/local/share/corporate-ca.crt
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/corporate-ca.crt

COPY package.json package-lock.json ./
RUN npm ci --include=dev --no-audit

COPY . .
RUN npm run build

FROM node:24-alpine
WORKDIR /app

RUN apk add --no-cache wget

RUN --mount=type=bind,source=certs,target=/tmp/certs \
    cat /tmp/certs/*-ca.pem > /usr/local/share/corporate-ca.crt 2>/dev/null \
    || touch /usr/local/share/corporate-ca.crt
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/corporate-ca.crt

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit

COPY --from=builder /app/dist ./dist/
COPY --from=builder /app/views ./views/
COPY configs ./configs/
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8090
ENTRYPOINT ["docker-entrypoint.sh"]
