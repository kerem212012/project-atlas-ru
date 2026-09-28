FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.5
RUN useradd --create-home --uid 10001 appuser
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY --chown=appuser:appuser app ./app
RUN mkdir -p /app/uploads && chown appuser:appuser /app/uploads
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8000
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
