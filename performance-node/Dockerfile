FROM python:3.13-slim

WORKDIR /app
RUN apt-get update && \
    apt-get install --no-install-recommends --yes stunnel4 && \
    rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && \
    useradd --create-home --uid 10001 --shell /usr/sbin/nologin nodeagent && \
    mkdir -p /var/lib/performance-node && chmod 0700 /var/lib/performance-node && \
    chown nodeagent:nodeagent /var/lib/performance-node

USER nodeagent
ENV PERFORMANCE_NODE_STATE_DIR=/var/lib/performance-node
ENV PERFORMANCE_STUNNEL_BINARY=/usr/bin/stunnel4
ENTRYPOINT ["python", "-m", "performance_node"]
CMD ["run"]
