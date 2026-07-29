FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 app && mkdir /data && chown app /data
USER app
CMD ["mailmerge-unsubscribe"]

