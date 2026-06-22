FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot ./bot

RUN useradd --create-home --uid 1000 botuser
USER botuser

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import pathlib, sys; sys.exit(0 if pathlib.Path('/proc/1/cmdline').read_bytes().replace(b'\0', b' ').startswith(b'python -m bot.main') else 1)"

CMD ["python", "-m", "bot.main"]
