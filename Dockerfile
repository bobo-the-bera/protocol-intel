FROM python:3.13-slim
RUN pip install --no-cache-dir uv==0.11.33
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev && useradd --uid 10001 --create-home monitor && mkdir -p /app/data && chown -R monitor:monitor /app/data
USER monitor
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
ENTRYPOINT ["protocol-intel"]
CMD ["worker"]
