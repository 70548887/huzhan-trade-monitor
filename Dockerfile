FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

RUN groupadd --system --gid 10001 monitor \
    && useradd --system --uid 10001 --gid monitor --no-create-home monitor

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=monitor:monitor huzhan_monitor ./huzhan_monitor
RUN mkdir -p /app/data /app/logs && chown monitor:monitor /app/data /app/logs

USER monitor
EXPOSE 8878

CMD ["python", "-m", "uvicorn", "huzhan_monitor.web:app", "--host", "0.0.0.0", "--port", "8878"]
