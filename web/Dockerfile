FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG SITE_URL=https://clipeo.app
ARG SITE_OWNER=
ARG SITE_CONTACT=
ENV NEXT_TELEMETRY_DISABLED=1 SITE_URL=$SITE_URL SITE_OWNER=$SITE_OWNER SITE_CONTACT=$SITE_CONTACT
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ARG SITE_URL=https://clipeo.app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0 SITE_URL=$SITE_URL
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
