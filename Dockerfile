# --- Dashboard (Vite + React + Tailwind) ---
FROM node:22-alpine AS ui
WORKDIR /ui
COPY web/package.json web/package-lock.json ./
RUN npm ci --fund=false --audit=false
COPY web/ ./
RUN npm run build

# --- API + agent ---
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent ./agent
COPY api ./api
COPY agentic_ai_hackthon_2026_sample_data-main ./agentic_ai_hackthon_2026_sample_data-main
COPY --from=ui /ui/dist ./web/dist

EXPOSE 8000

CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
