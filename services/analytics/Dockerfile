FROM python:3.12.16-slim-trixie
WORKDIR /app
COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt \
    && useradd --system --uid 10001 --no-create-home analytics
COPY pymentor_analytics ./pymentor_analytics
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
USER 10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "pymentor_analytics.app.cli"]
CMD ["collector", "--host", "0.0.0.0"]
