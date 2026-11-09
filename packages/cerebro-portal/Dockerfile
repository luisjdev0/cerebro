FROM node:22-alpine AS build
RUN apk add --no-cache git && corepack enable
# The lockfile records the Ixchel dependency with an ssh URL; no ssh/keys in the
# build, so fetch it over anonymous https (the repo is public).
RUN git config --global url."https://github.com/".insteadOf "git@github.com:" \
 && git config --global --add url."https://github.com/".insteadOf "ssh://git@github.com/"
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1/healthz || exit 1
