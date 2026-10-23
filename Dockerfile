# Central preset management and MCP. Slicing needs a separate supported runtime.
FROM docker.io/library/python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE.md ./
COPY src/ ./src/
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir .
ENV HOME=/home/mcp
RUN mkdir -p /home/mcp /workspace && chown 65532:65532 /home/mcp /workspace
ENV BAMBUSTUDIO_WORKSPACE=/workspace
USER 65532:65532
CMD ["bambustudio-mcp"]
