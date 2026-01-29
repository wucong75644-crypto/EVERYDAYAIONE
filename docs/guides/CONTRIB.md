# Contributing Guide

> **Last Updated**: 2026-01-29
> **Source of Truth**: `frontend/package.json`, `backend/requirements.txt`, `.env.example`

## Development Workflow

### Prerequisites

**Frontend Requirements**:
- Node.js 18+ (recommended: use nvm)
- pnpm, npm, or yarn

**Backend Requirements**:
- Python 3.11+
- Virtual environment (venv)
- Redis (for task queue and rate limiting)
- Supabase account

### Initial Setup

#### 1. Clone and Install

```bash
# Clone repository
git clone <repository-url>
cd EVERYDAYAIONE

# Frontend setup
cd frontend
npm install  # or pnpm install / yarn install
cd ..

# Backend setup
cd backend
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd ..
```

#### 2. Environment Configuration

**Frontend** (`.env` in `frontend/`):
```bash
# Copy example file
cp frontend/.env.example frontend/.env

# Edit with your values
VITE_API_BASE_URL=http://localhost:8000/api
```

**Backend** (`.env` in `backend/`):
```bash
# Copy example file
cp backend/.env.example backend/.env

# Configure required variables (see Environment Variables section)
```

**Root** (`.env` in root directory):
```bash
# Same as backend/.env
cp .env.example .env
```

### Available Scripts

#### Frontend Scripts

| Script | Command | Description |
|--------|---------|-------------|
| `dev` | `npm run dev` | Start development server with hot reload (Vite) |
| `build` | `npm run build` | TypeScript compile + production build |
| `lint` | `npm run lint` | Run ESLint code quality checks |
| `preview` | `npm run preview` | Preview production build locally |
| `test` | `npm run test` | Run tests in watch mode (Vitest) |
| `test:run` | `npm run test:run` | Run tests once (CI mode) |
| `test:coverage` | `npm run test:coverage` | Generate test coverage report |

#### Backend Scripts

| Script | Command | Description |
|--------|---------|-------------|
| Start Server | `uvicorn main:app --reload --host 0.0.0.0 --port 8000` | Start FastAPI dev server with hot reload |
| Run Tests | `pytest` | Run all tests |
| Run Tests (Async) | `pytest -v` | Run tests with verbose output |
| Test Coverage | `pytest --cov=. --cov-report=html` | Generate coverage report |

### Development Server

```bash
# Terminal 1: Backend (from backend/)
cd backend
source venv/bin/activate
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2: Frontend (from frontend/)
cd frontend
npm run dev

# Access application
# Frontend: http://localhost:5173
# Backend API: http://localhost:8000
# API Docs: http://localhost:8000/docs
```

### Testing Procedures

#### Frontend Testing

```bash
cd frontend

# Run tests in watch mode (development)
npm run test

# Run tests once (CI)
npm run test:run

# Generate coverage report
npm run test:coverage
# Coverage report: frontend/coverage/index.html
```

**Testing Stack**:
- **Framework**: Vitest
- **Utilities**: @testing-library/react, @testing-library/user-event
- **DOM**: jsdom

**Coverage Requirements**:
- Minimum: 80% overall coverage
- Critical paths (auth, chat, API): 90%+

#### Backend Testing

```bash
cd backend
source venv/bin/activate

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_auth.py

# Run with coverage
pytest --cov=. --cov-report=html
# Coverage report: backend/htmlcov/index.html
```

**Testing Stack**:
- **Framework**: pytest
- **Async Support**: pytest-asyncio

### Code Quality Standards

#### Pre-commit Checklist

- [ ] Code passes linting (`npm run lint` / ESLint for Python)
- [ ] All tests pass (`npm run test:run` / `pytest`)
- [ ] Test coverage ≥80%
- [ ] No console.log in production code
- [ ] No hardcoded secrets (check with `git diff`)
- [ ] Environment variables documented in `.env.example`
- [ ] Type annotations for Python functions
- [ ] TypeScript strict mode compliance

#### Code Style

**TypeScript/React**:
- Use functional components with hooks
- Prefer immutability (never mutate objects/arrays)
- Keep files <800 lines
- Keep functions <50 lines
- Max nesting depth: 4 levels
- Use Zustand for global state
- CSS: Tailwind utility classes (no global styles)

**Python**:
- Follow PEP 8
- Type annotations required for public functions
- Use async/await for I/O operations
- Error handling with try-except + loguru
- Business context in logs (user_id, provider, etc.)

### Debugging

#### Frontend Debugging

```bash
# Browser DevTools
# - React DevTools extension
# - Network tab for API calls
# - Console for errors

# VSCode Launch Configuration
# Create .vscode/launch.json:
{
  "type": "chrome",
  "request": "launch",
  "name": "Launch Chrome",
  "url": "http://localhost:5173",
  "webRoot": "${workspaceFolder}/frontend"
}
```

#### Backend Debugging

```bash
# Enable debug logging
# In .env:
APP_DEBUG=true

# Logs location
backend/backend.log

# VSCode Launch Configuration
{
  "type": "python",
  "request": "launch",
  "module": "uvicorn",
  "args": ["main:app", "--reload"],
  "cwd": "${workspaceFolder}/backend"
}
```

### Common Issues

#### Issue: Frontend can't connect to backend

**Solution**:
1. Check backend is running: `curl http://localhost:8000/health`
2. Verify `VITE_API_BASE_URL` in `frontend/.env`
3. Check CORS settings in `backend/main.py`

#### Issue: Backend database connection fails

**Solution**:
1. Verify Supabase credentials in `.env`
2. Check network connectivity to Supabase
3. Ensure service role key is correct

#### Issue: Redis connection timeout

**Solution**:
1. Verify Redis host/port/password in `.env`
2. For Upstash: Ensure `REDIS_SSL=true`
3. Test connection: `redis-cli -h <host> -p <port> -a <password>`

#### Issue: Tests fail due to environment variables

**Solution**:
1. Create `.env.test` with test values
2. Or mock environment in test files
3. Never use production credentials in tests

### Git Workflow

```bash
# Create feature branch
git checkout -b feat/your-feature-name

# Make changes and commit
git add <files>
git commit -m "feat: Add feature description"

# Push and create PR
git push -u origin feat/your-feature-name
```

**Commit Message Format**:
```
<type>: <description>

<optional body>
```

**Types**: feat, fix, refactor, docs, test, chore, perf, ci

### Documentation Updates

When modifying code, update these docs:
- **FUNCTION_INDEX.md**: New/changed functions
- **API_REFERENCE.md**: API endpoint changes
- **CURRENT_ISSUES.md**: Known bugs or blockers
- **PROJECT_OVERVIEW.md**: Architecture changes
- **.env.example**: New environment variables

### Getting Help

- **Documentation**: Start with `docs/README.md`
- **API Reference**: `docs/API_REFERENCE.md`
- **Function Index**: `docs/FUNCTION_INDEX.md`
- **Current Issues**: `docs/CURRENT_ISSUES.md`

---

**Next Steps**: See [RUNBOOK.md](RUNBOOK.md) for deployment and operations procedures.
