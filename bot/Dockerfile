# Meeting bot: Node.js + Playwright Chromium. Not published to the internet; only the backend talks to it.
FROM node:22-bookworm-slim

ENV NODE_ENV=production PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY package.json package-lock.json* ./
# `npm ci` when a lockfile is present (reproducible), otherwise `npm install`.
RUN if [ -f package-lock.json ]; then npm ci --omit=dev; else npm install --omit=dev; fi \
 && npx playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 smmg \
 && chown -R smmg /app /ms-playwright

COPY src ./src
USER smmg
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "src/index.js"]
