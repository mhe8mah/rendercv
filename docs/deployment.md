# RenderCV SaaS Deployment Guide

This guide covers deploying RenderCV as a SaaS application on your VPS.

## Prerequisites

- A VPS with at least 2GB RAM and 2 CPU cores
- Docker and Docker Compose installed
- A domain name (optional but recommended for SSL)
- Basic knowledge of Linux administration

## Quick Start

### 1. Clone and Configure

```bash
# Clone the repository
git clone https://github.com/rendercv/rendercv.git
cd rendercv

# Copy the environment file
cp .env.example .env

# Edit the environment file with your settings
nano .env
```

### 2. Configure Essential Settings

Edit `.env` with at minimum:

```bash
# REQUIRED: Generate a secure secret key
SECRET_KEY=$(openssl rand -hex 32)

# REQUIRED: Set a strong database password
DB_PASSWORD=your-secure-password

# RECOMMENDED: Set your environment
ENVIRONMENT=production
DEBUG=false
```

### 3. Start the Services

```bash
# Start all services in detached mode
docker compose up -d

# Check service status
docker compose ps

# View logs
docker compose logs -f
```

### 4. Initialize the Database

The database is automatically initialized on first startup. To create an admin user:

```bash
# Access the API container
docker compose exec api python -c "
from rendercv.web.database import async_session_maker
from rendercv.web.models import User
from rendercv.web.auth import hash_password
import asyncio

async def create_admin():
    async with async_session_maker() as session:
        admin = User(
            email='admin@yourdomain.com',
            hashed_password=hash_password('your-secure-password'),
            full_name='Admin User',
            is_active=True,
            is_verified=True,
            is_superuser=True,
            tier='enterprise'
        )
        session.add(admin)
        await session.commit()
        print(f'Admin user created: {admin.email}')

asyncio.run(create_admin())
"
```

## Architecture

The SaaS deployment consists of four main services:

```
┌─────────────────────────────────────────────────────────────┐
│                         Nginx                                │
│                    (Reverse Proxy)                           │
│                    Port 80/443                               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       API Server                             │
│                    (FastAPI/Uvicorn)                         │
│                      Port 8000                               │
└─────────────────────────────────────────────────────────────┘
         │                                    │
         ▼                                    ▼
┌─────────────────────┐            ┌─────────────────────┐
│    PostgreSQL       │            │       Redis         │
│    (Database)       │            │   (Job Queue)       │
│    Port 5432        │            │    Port 6379        │
└─────────────────────┘            └─────────────────────┘
                                             │
                                             ▼
                                   ┌─────────────────────┐
                                   │      Worker         │
                                   │ (Background Jobs)   │
                                   └─────────────────────┘
```

## Production Configuration

### Enable SSL with Let's Encrypt

1. Update your domain in `.env`:
```bash
DOMAIN=your-domain.com
```

2. Update nginx configuration:
```bash
# Edit nginx/conf.d/default.conf
# Uncomment the HTTPS server block
# Update 'your-domain.com' with your actual domain
```

3. Obtain SSL certificate:
```bash
# Start with production profile
docker compose --profile production up -d

# Run certbot for initial certificate
docker compose exec certbot certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    -d your-domain.com \
    --email your-email@domain.com \
    --agree-tos \
    --no-eff-email

# Restart nginx to apply certificate
docker compose restart nginx
```

### Scaling Workers

For higher throughput, scale the worker service:

```bash
docker compose up -d --scale worker=3
```

### Resource Limits

Add resource limits in `docker-compose.yml`:

```yaml
services:
  api:
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 1G
        reservations:
          cpus: '0.5'
          memory: 512M
```

## Backup and Recovery

### Database Backup

```bash
# Create a backup
docker compose exec db pg_dump -U rendercv rendercv > backup_$(date +%Y%m%d).sql

# Automated backup script (add to crontab)
0 2 * * * cd /path/to/rendercv && docker compose exec -T db pg_dump -U rendercv rendercv > /backups/rendercv_$(date +\%Y\%m\%d).sql
```

### Restore Database

```bash
# Restore from backup
cat backup_20240101.sql | docker compose exec -T db psql -U rendercv rendercv
```

### Storage Backup

```bash
# Backup rendered files
tar -czvf storage_backup_$(date +%Y%m%d).tar.gz /path/to/rendercv/storage/
```

## Monitoring

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f api

# Last 100 lines
docker compose logs --tail 100 api
```

### Health Checks

```bash
# Check API health
curl http://localhost:8000/api/v1/health/

# Check readiness
curl http://localhost:8000/api/v1/health/ready

# Check liveness
curl http://localhost:8000/api/v1/health/live
```

### Queue Status (Admin)

```bash
# Login and get token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@domain.com","password":"password"}' | jq -r .access_token)

# Check queue stats
curl -H "Authorization: Bearer $TOKEN" \
    http://localhost:8000/api/v1/health/queues
```

## Updating

### Standard Update

```bash
# Pull latest changes
git pull origin main

# Rebuild and restart
docker compose build
docker compose up -d

# Run migrations if any
docker compose exec api python -c "
from rendercv.web.database import init_db
import asyncio
asyncio.run(init_db())
"
```

### Zero-Downtime Update

```bash
# Build new images
docker compose build

# Scale up new workers
docker compose up -d --scale worker=4

# Rolling update API (using Docker Swarm)
docker service update --image rendercv-api:latest rendercv_api
```

## Troubleshooting

### Common Issues

**Container won't start:**
```bash
# Check logs
docker compose logs api

# Check container status
docker compose ps -a
```

**Database connection errors:**
```bash
# Verify database is healthy
docker compose exec db pg_isready -U rendercv

# Check connection from API
docker compose exec api python -c "
from rendercv.web.database import engine
import asyncio
async def test():
    async with engine.connect() as conn:
        await conn.execute('SELECT 1')
        print('Database connection OK')
asyncio.run(test())
"
```

**Redis connection errors:**
```bash
# Check Redis is responding
docker compose exec redis redis-cli ping
```

**Render jobs stuck:**
```bash
# Check worker logs
docker compose logs worker

# Restart workers
docker compose restart worker
```

### Performance Tuning

**Increase worker count:**
```bash
docker compose up -d --scale worker=4
```

**Optimize PostgreSQL:**
```bash
# Add to docker-compose.yml under db service
command: >
    postgres
    -c max_connections=200
    -c shared_buffers=256MB
    -c effective_cache_size=768MB
```

**Increase Uvicorn workers:**
```yaml
# In docker-compose.yml
services:
  api:
    command: uvicorn rendercv.web.app:app --host 0.0.0.0 --port 8000 --workers 4
```

## Security Checklist

- [ ] Change default `SECRET_KEY`
- [ ] Set strong `DB_PASSWORD`
- [ ] Enable HTTPS with valid SSL certificate
- [ ] Configure firewall (only expose ports 80/443)
- [ ] Set `DEBUG=false` in production
- [ ] Regular security updates for Docker images
- [ ] Enable rate limiting in nginx
- [ ] Set up log rotation
- [ ] Configure automated backups
- [ ] Monitor for suspicious activity

## API Documentation

Once deployed, access the interactive API documentation at:
- **Swagger UI**: `https://your-domain.com/docs`
- **ReDoc**: `https://your-domain.com/redoc`
- **OpenAPI JSON**: `https://your-domain.com/api/openapi.json`

## Support

For issues and feature requests, please open an issue on GitHub:
https://github.com/rendercv/rendercv/issues
