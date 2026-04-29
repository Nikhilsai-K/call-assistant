FROM node:20-alpine
WORKDIR /app
RUN corepack enable
COPY package.json ./
RUN pnpm install
COPY . /app
EXPOSE 3000
CMD ["pnpm", "dev"]
