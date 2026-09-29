FROM node:20-slim AS build
WORKDIR /app

COPY package.json package-lock.json ./
# --include=dev: vite/vitest are devDependencies; without this, npm skips them
# whenever NODE_ENV=production or an npm "omit=dev" setting is active.
RUN npm ci --include=dev --no-audit --no-fund \
 && (test -x node_modules/.bin/vite || (echo "### vite missing after npm ci ###"; env | grep -i -E "node_env|npm_config"; exit 1))

COPY . .

ARG VITE_API_BASE=http://localhost:8000
ARG VITE_WS_BASE=ws://localhost:8000
ENV VITE_API_BASE=$VITE_API_BASE
ENV VITE_WS_BASE=$VITE_WS_BASE

RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
