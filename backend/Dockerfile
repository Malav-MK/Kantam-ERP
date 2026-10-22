FROM python:3.12.8-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 kantam
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=kantam:kantam . .
RUN mkdir -p /data/media /data/keys && chown -R kantam:kantam /data
USER kantam
ENV ZT_KEY_DIR=/data/keys KANTAM_MEDIA_ROOT=/data/media
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn kantam.wsgi -b 0.0.0.0:8000 -w ${WEB_WORKERS:-4} --timeout 120 --access-logfile -"]
