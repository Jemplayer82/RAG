FROM ghcr.io/jemplayer82/rag:latest

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Remove the following line, it's not needed in Docker-Compose setup
# EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app_fastapi:app"]

# nginx.conf
user  nginx;
worker_processes  4;

error_log  /var/log/nginx/error.log notice;
pid        /var/run/nginx.pid;

events {
    worker_connections  1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    log_format  main  '$remote_addr - $remote_user [$time_iso8601] "$request" '
                      '$status $body_bytes_sent "$http_referer" '
                      '"$http_user_agent" "$http_x_forwarded_for"';

    access_log  /var/log/nginx/access.log  main;

    sendfile        on;
    keepalive_timeout  65;

    gzip  on;

    upstream rag {
        server rag:8000;
    }

    server {
        listen 80;
        server_name yourdomain.com;

        location / {
            proxy_pass http://rag;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        location /.well-known/acme-challenge/ {
            root /var/www/certbot;
        }
    }

    server {
        listen 443 ssl;
        server_name yourdomain.com;

        ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

        location / {
            proxy_pass http://rag;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }

# docker-compose.yml
version: '3.8'

services:
  rag:
    image: ghcr.io/jemplayer82/rag:latest
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
    environment:
      - RAG_ENV=production
    depends_on:
      - db
    restart: unless-stopped

  rag-worker:
    image: ghcr.io/jemplayer82/rag:latest
    volumes:
      - ./data:/app/data
    environment:
      - RAG_ENV=production
    restart: unless-stopped

  db:
    image: postgres:15
    environment:
      POSTGRES_USER: rag
      POSTGRES_PASSWORD: rag
      POSTGRES_DB: rag
    volumes:
      - ./db:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    restart: unless-stopped

  nginx:
    image: nginx:latest
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./certbot_data:/etc/letsencrypt
      - ./certbot_www:/var/www/certbot
    depends_on:
      - rag
    restart: unless-stopped

  certbot:
    image: certbot/certbot:latest
    volumes:
      - ./certbot_data:/etc/letsencrypt
      - ./certbot_www:/var/www/certbot
    command: certbot certonly --standalone --agree-tos --email your@email.com -d yourdomain.com
    restart: unless-stopped
# Modified routes
@app_fastapi.add_route("/api/sources", methods=["POST"])
async def handle_sources(data: dict):
    # Handle file uploads here

@app_fastapi.add_route("/api/sources/jobs/{job_id}", methods=["GET"])
async def get_job_status(job_id: str):
    # Handle job status check here

@app_fastapi.add_route("/api/library", methods=["GET"])
async def handle_library():
    # Handle library request here

