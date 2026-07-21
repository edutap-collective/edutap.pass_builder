FROM python:3.14-slim AS build
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache .

FROM python:3.14-slim
RUN useradd --create-home --uid 10001 app
COPY --from=build /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY assets /app/assets
WORKDIR /app
USER app
EXPOSE 8000
CMD ["uvicorn", "edutap.pass_builder.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
