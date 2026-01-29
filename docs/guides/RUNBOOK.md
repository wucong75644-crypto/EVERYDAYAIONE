# Operations Runbook

> **Last Updated**: 2026-01-29
> **Audience**: DevOps, SREs, On-call Engineers
> **Source of Truth**: `.env.example`, deployment configurations

## Table of Contents

1. [Deployment Procedures](#deployment-procedures)
2. [Monitoring and Alerts](#monitoring-and-alerts)
3. [Common Issues](#common-issues)
4. [Rollback Procedures](#rollback-procedures)
5. [Emergency Contacts](#emergency-contacts)

---

## Deployment Procedures

### Pre-deployment Checklist

- [ ] All tests pass (`npm run test:run` + `pytest`)
- [ ] Test coverage ≥80%
- [ ] Database migrations tested
- [ ] Environment variables verified in `.env.example`
- [ ] API backward compatibility maintained
- [ ] Security scan completed (no secrets in code)
- [ ] Staging deployment successful
- [ ] Rollback plan documented

### Frontend Deployment

#### Build Process

```bash
cd frontend

# 1. Install dependencies
npm install

# 2. Run tests
npm run test:run

# 3. Build production bundle
npm run build
# Output: frontend/dist/

# 4. Verify build
npm run preview
# Test at http://localhost:4173
```

#### Deployment Steps

**Option A: Static Hosting (Vercel/Netlify)**

```bash
# Vercel
vercel --prod

# Netlify
netlify deploy --prod --dir=dist
```

**Option B: CDN/S3**

```bash
# AWS S3 + CloudFront
aws s3 sync dist/ s3://your-bucket-name/ --delete
aws cloudfront create-invalidation --distribution-id YOUR_ID --paths "/*"

# Alibaba Cloud OSS (if configured)
# Upload dist/ to OSS bucket
# Configure CDN acceleration
```

**Environment Variables** (set in deployment platform):
```bash
VITE_API_BASE_URL=https://api.yourdomain.com/api
```

### Backend Deployment

#### Build Process

```bash
cd backend

# 1. Activate virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run tests
pytest

# 4. Test server locally
uvicorn main:app --host 0.0.0.0 --port 8000
```

#### Deployment Steps

**Option A: Docker**

```bash
# 1. Build image
docker build -t everydayai-backend:latest .

# 2. Run container
docker run -d \
  -p 8000:8000 \
  --env-file .env.production \
  --name everydayai-backend \
  everydayai-backend:latest

# 3. Verify health
curl http://localhost:8000/health
```

**Option B: Systemd Service**

```bash
# 1. Copy code to server
rsync -avz --exclude venv backend/ user@server:/opt/everydayai/

# 2. Create systemd service
sudo nano /etc/systemd/system/everydayai.service
```

```ini
[Unit]
Description=EverydayAI Backend
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/everydayai
Environment="PATH=/opt/everydayai/venv/bin"
ExecStart=/opt/everydayai/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
# 3. Enable and start
sudo systemctl enable everydayai
sudo systemctl start everydayai
sudo systemctl status everydayai

# 4. Verify
curl http://localhost:8000/health
```

#### Database Migrations

```bash
# Supabase migrations
# 1. Create migration file in docs/database/migrations/
# 2. Apply via Supabase dashboard or CLI

# Example: Add new column
# File: docs/database/migrations/20260129_add_user_preferences.sql
ALTER TABLE users ADD COLUMN preferences JSONB DEFAULT '{}'::jsonb;

# Apply migration
supabase db push
```

### Environment Variables (Production)

**Critical Variables** (must be set):

```bash
# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=<production-anon-key>
SUPABASE_SERVICE_ROLE_KEY=<production-service-role-key>

# JWT (MUST be different from dev)
JWT_SECRET_KEY=<strong-random-secret-min-32-chars>
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Redis (Upstash recommended for production)
REDIS_HOST=your-redis.upstash.io
REDIS_PORT=6379
REDIS_PASSWORD=<production-password>
REDIS_DB=0
REDIS_SSL=true

# Aliyun SMS
ALIYUN_SMS_ACCESS_KEY_ID=<production-key-id>
ALIYUN_SMS_ACCESS_KEY_SECRET=<production-secret>
ALIYUN_SMS_SIGN_NAME=<approved-sign-name>
ALIYUN_SMS_TEMPLATE_REGISTER=SMS_<template-code>
ALIYUN_SMS_TEMPLATE_RESET_PWD=SMS_<template-code>
ALIYUN_SMS_TEMPLATE_BIND_PHONE=SMS_<template-code>

# Aliyun OSS
OSS_ACCESS_KEY_ID=<production-key-id>
OSS_ACCESS_KEY_SECRET=<production-secret>
OSS_BUCKET_NAME=<production-bucket>
OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
OSS_REGION=cn-hangzhou

# KIE API
KIE_API_KEY=<production-api-key>
KIE_BASE_URL=https://api.kie.ai/v1

# Application
APP_ENV=production
APP_DEBUG=false  # CRITICAL: Must be false in production
APP_HOST=0.0.0.0
APP_PORT=8000

# Rate Limiting
RATE_LIMIT_GLOBAL_TASKS=15
RATE_LIMIT_CONVERSATION_TASKS=5
```

**Security Notes**:
- Never commit `.env` to git
- Use secrets management (AWS Secrets Manager, HashiCorp Vault, etc.)
- Rotate keys quarterly
- Use different keys for staging/production

---

## Monitoring and Alerts

### Health Check Endpoints

```bash
# Backend health
curl https://api.yourdomain.com/health
# Expected: {"status": "healthy"}

# Database connection
curl https://api.yourdomain.com/health/db
# Expected: {"status": "connected"}

# Redis connection
curl https://api.yourdomain.com/health/redis
# Expected: {"status": "connected"}
```

### Key Metrics to Monitor

#### Application Metrics

| Metric | Warning Threshold | Critical Threshold | Action |
|--------|------------------|-------------------|--------|
| API Response Time (p95) | >500ms | >1000ms | Check database queries, Redis cache |
| Error Rate | >1% | >5% | Check logs, recent deployments |
| Request Rate | >10000/min | >50000/min | Scale horizontally, enable rate limiting |
| CPU Usage | >70% | >90% | Scale up/out |
| Memory Usage | >80% | >95% | Check memory leaks, restart service |

#### Database Metrics

| Metric | Warning Threshold | Critical Threshold | Action |
|--------|------------------|-------------------|--------|
| Connection Pool Usage | >70% | >90% | Increase pool size |
| Query Response Time | >100ms | >500ms | Analyze slow queries, add indexes |
| Disk Usage | >80% | >95% | Archive old data, increase storage |

#### Redis Metrics

| Metric | Warning Threshold | Critical Threshold | Action |
|--------|------------------|-------------------|--------|
| Memory Usage | >80% | >95% | Check TTL policies, eviction strategy |
| Connection Count | >80% of max | >95% of max | Increase connection limit |
| Eviction Rate | >100/sec | >1000/sec | Increase memory, review TTL |

### Log Monitoring

**Log Locations**:
- Backend: `backend/backend.log`
- System: `/var/log/everydayai/`
- Container: `docker logs everydayai-backend`

**Critical Log Patterns** (set up alerts):

```bash
# Authentication failures
grep "Authentication failed" backend.log

# Database errors
grep "Database connection" backend.log

# API errors (5xx)
grep "status_code=5" backend.log

# Rate limit exceeded
grep "Rate limit exceeded" backend.log

# KIE API failures
grep "KIE API error" backend.log
```

### Alert Configuration

**Recommended Alerting Tool**: PagerDuty, Datadog, CloudWatch, or custom

**Alert Rules**:

1. **P0 (Critical - Immediate Response)**
   - Service down (health check fails 3x in 5min)
   - Error rate >5% for 5min
   - Database connection lost
   - Redis connection lost

2. **P1 (High - Response within 30min)**
   - API p95 response time >1s for 10min
   - Error rate >1% for 10min
   - Disk usage >95%
   - Memory usage >95%

3. **P2 (Medium - Response within 2h)**
   - API p95 response time >500ms for 15min
   - CPU usage >70% for 15min
   - Memory usage >80% for 15min

---

## Common Issues

### Issue 1: High API Response Time

**Symptoms**:
- API requests taking >1s
- Frontend showing loading spinners
- Users reporting slow performance

**Diagnosis**:
```bash
# Check database slow queries
# In Supabase dashboard: Logs > Slow Queries

# Check Redis latency
redis-cli --latency -h <host> -p <port> -a <password>

# Check backend logs for slow operations
grep "took.*ms" backend.log | sort -k3 -nr | head -20
```

**Resolution**:
1. **Database Optimization**:
   - Add missing indexes
   - Optimize N+1 queries
   - Enable connection pooling

2. **Caching**:
   - Implement Redis caching for hot data
   - Use CDN for static assets

3. **Scaling**:
   - Horizontal scaling: Add more backend instances
   - Vertical scaling: Increase CPU/memory

### Issue 2: Authentication Failures

**Symptoms**:
- Users can't log in
- 401 Unauthorized errors
- JWT validation failures

**Diagnosis**:
```bash
# Check JWT secret consistency
echo $JWT_SECRET_KEY | wc -c  # Should be ≥32 characters

# Check token expiration
# Decode JWT at jwt.io or:
python3 -c "import jwt; print(jwt.decode('<token>', verify=False))"

# Check backend logs
grep "Authentication" backend.log | tail -50
```

**Resolution**:
1. Verify `JWT_SECRET_KEY` matches across all instances
2. Check system clock sync (NTP)
3. Verify token not expired
4. Check Supabase API key validity

### Issue 3: Redis Connection Timeouts

**Symptoms**:
- Rate limiting not working
- Task queue failures
- Connection timeout errors

**Diagnosis**:
```bash
# Test Redis connection
redis-cli -h <host> -p <port> -a <password> ping
# Expected: PONG

# Check connection count
redis-cli -h <host> -p <port> -a <password> INFO clients

# Check backend logs
grep "Redis" backend.log | tail -50
```

**Resolution**:
1. **Connection Issues**:
   - Verify `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`
   - Check `REDIS_SSL=true` for Upstash
   - Verify firewall rules

2. **Connection Pool Exhaustion**:
   - Increase connection pool size
   - Check for connection leaks

3. **Memory Issues**:
   - Check Redis memory usage
   - Adjust eviction policy

### Issue 4: File Upload Failures

**Symptoms**:
- Users can't upload images/files
- OSS upload errors
- 500 Internal Server Error

**Diagnosis**:
```bash
# Check OSS credentials
curl -I https://<bucket>.<endpoint>/<test-file>

# Check backend logs
grep "OSS" backend.log | tail -50

# Verify file size limits
grep "File too large" backend.log
```

**Resolution**:
1. Verify OSS credentials (`OSS_ACCESS_KEY_ID`, `OSS_ACCESS_KEY_SECRET`)
2. Check bucket permissions (public read if needed)
3. Verify CORS configuration
4. Check file size limits (frontend + backend)

### Issue 5: Database Connection Pool Exhausted

**Symptoms**:
- "Too many connections" errors
- API requests timing out
- Database connection errors

**Diagnosis**:
```bash
# Check active connections in Supabase dashboard
# Database > Settings > Connection Pooling

# Check backend connection pool usage
grep "connection pool" backend.log
```

**Resolution**:
1. Increase connection pool size in Supabase
2. Enable connection pooling (pgBouncer)
3. Fix connection leaks in code
4. Scale backend horizontally

---

## Rollback Procedures

### Frontend Rollback

**Scenario**: New deployment breaks UI

```bash
# Vercel: Revert to previous deployment
vercel rollback

# Netlify: Roll back via dashboard
# Deploys > Previous deploy > Publish deploy

# S3/CDN: Restore previous build
aws s3 sync s3://your-backup-bucket/previous-build/ s3://your-bucket-name/ --delete
aws cloudfront create-invalidation --distribution-id YOUR_ID --paths "/*"
```

**Time to Rollback**: ~5 minutes

### Backend Rollback

**Scenario**: New deployment causes errors

**Option A: Docker**

```bash
# 1. Stop current container
docker stop everydayai-backend

# 2. Start previous version
docker run -d \
  -p 8000:8000 \
  --env-file .env.production \
  --name everydayai-backend \
  everydayai-backend:previous-tag

# 3. Verify health
curl http://localhost:8000/health
```

**Option B: Systemd**

```bash
# 1. Stop service
sudo systemctl stop everydayai

# 2. Restore previous code
cd /opt/everydayai
git checkout <previous-commit-hash>

# 3. Restart service
sudo systemctl start everydayai

# 4. Verify health
curl http://localhost:8000/health
```

**Time to Rollback**: ~10 minutes

### Database Rollback

**Scenario**: Migration causes issues

**⚠️ CRITICAL**: Database rollbacks are dangerous. Always test in staging first.

```sql
-- Example: Reverse "add column" migration
ALTER TABLE users DROP COLUMN preferences;

-- Example: Reverse "add index" migration
DROP INDEX idx_users_email;
```

**Process**:
1. Create rollback migration file
2. Test in staging
3. Apply in production during maintenance window
4. Verify data integrity

**Time to Rollback**: ~30 minutes (depends on table size)

---

## Emergency Contacts

### On-Call Rotation

| Role | Primary | Secondary |
|------|---------|-----------|
| Backend | [Name] [Phone] | [Name] [Phone] |
| Frontend | [Name] [Phone] | [Name] [Phone] |
| Database | [Name] [Phone] | [Name] [Phone] |
| DevOps | [Name] [Phone] | [Name] [Phone] |

### Escalation Path

1. **P0 (Critical)**: Immediate notification to on-call engineer
2. **P1 (High)**: Notification within 30 minutes
3. **P2 (Medium)**: Next business day

### External Services

| Service | Contact | Documentation |
|---------|---------|---------------|
| Supabase | support@supabase.io | https://supabase.com/docs |
| Upstash (Redis) | support@upstash.com | https://docs.upstash.com |
| Aliyun | International: +65 6591 7888 | https://www.alibabacloud.com/help |
| KIE API | [Support contact] | [API docs URL] |

---

## Maintenance Windows

**Scheduled Maintenance**: Every Sunday 02:00-04:00 UTC

**Maintenance Procedures**:
1. Announce maintenance 48h in advance
2. Display maintenance banner on frontend
3. Perform upgrades/migrations
4. Run smoke tests
5. Monitor for 1h post-maintenance

---

**Related Documents**:
- [Contributing Guide](CONTRIB.md) - Development and testing procedures
- [API Reference](../API_REFERENCE.md) - API endpoint documentation
- [Current Issues](../CURRENT_ISSUES.md) - Known bugs and workarounds
