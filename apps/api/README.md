# API (FastAPI)

See the root [README.md](../../README.md) for full run instructions. Quick start:

```bash
cd apps/api
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Health check: `GET http://localhost:8000/health`
