FROM node:22-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# the container must listen on all addresses; `npm run dev` alone stays on localhost
CMD ["npm", "run", "dev", "--", "--host"]
