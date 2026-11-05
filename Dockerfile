FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && useradd --system --uid 10001 app && mkdir /data && chown app /data
COPY server.py ./
COPY chat chat
USER 10001
ENV HOST=0.0.0.0 PORT=5000 CHAT_UPLOAD_DIR=/data
CMD ["python","server.py"]
