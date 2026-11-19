FROM docker.1ms.run/python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple

# MinerU 依赖的系统库
# libgl1 + libglib2.0-0: OpenCV 运行时
# libgomp1: OpenMP (torch / onnxruntime 并行计算)
RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        --index-url https://mirrors.aliyun.com/pypi/simple \
        uv

COPY pyproject.toml uv.lock .python-version ./

RUN uv sync --frozen

EXPOSE 18001

CMD ["uv", "run", "mineru-api", "--host", "0.0.0.0", "--port", "18001"]
