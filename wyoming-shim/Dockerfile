# 迁移到 PVE/独立 Docker 主机时使用；本机（WSL）由 systemd user service 托管
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY envutil.py server.py ./

EXPOSE 10300

# DASHSCOPE_API_KEY 通过 env_file / environment 注入，勿写入镜像
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import socket; socket.create_connection(('127.0.0.1', 10300), 3).close()"

CMD ["python", "server.py", "--uri", "tcp://0.0.0.0:10300"]
