# Build stage
FROM node:26-alpine AS build

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy source code
COPY . .

# Build the app
RUN npm run build

# Production stage
FROM nginx:stable-alpine

# Create non-root user and fix permissions
RUN addgroup -g 1001 -S appgroup && \
    adduser -S appuser -u 1001 -G appgroup && \
    chown -R appuser:appgroup /usr/share/nginx/html && \
    chown -R appuser:appgroup /var/cache/nginx && \
    chown -R appuser:appgroup /var/log/nginx && \
    touch /var/run/nginx.pid && \
    chown appuser:appgroup /var/run/nginx.pid

# Copy built app
COPY --from=build /app/dist /usr/share/nginx/html

# Copy nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy start script
COPY start.sh /start.sh
RUN chmod +x /start.sh && chown appuser:appgroup /start.sh

USER appuser

# Expose port
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost/ || exit 1

# Start with script
CMD ["/start.sh"]
