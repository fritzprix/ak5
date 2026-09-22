FROM python:3.13-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml README.md ./
COPY backend ./backend

RUN uv pip install --system -e backend

EXPOSE 8000

CMD ["uvicorn", "ak5.main:app", "--host", "0.0.0.0", "--port", "8000"]
