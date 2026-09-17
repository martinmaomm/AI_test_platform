FROM python:3.13-slim AS runtime-base

LABEL org.opencontainers.image.title="AI test platform performance node" \
      org.opencontainers.image.description="仅限测试使用 / For authorized testing only. Not for production." \
      org.opencontainers.image.version="0.2.0"

WORKDIR /app
RUN apt-get update && \
    apt-get install --no-install-recommends --yes stunnel4 && \
    rm -rf /var/lib/apt/lists/*

FROM runtime-base AS build
COPY pyproject.toml README.md ./
COPY src/performance_node/*.py ./src/performance_node/
RUN pip install --no-cache-dir . && \
    useradd --create-home --uid 10001 --shell /usr/sbin/nologin nodeagent && \
    mkdir -p /var/lib/performance-node && chmod 0700 /var/lib/performance-node && \
    chown nodeagent:nodeagent /var/lib/performance-node

# Third-party wheels may ship public test keys (for example gevent/tests).
# Clean the build stage, then copy only runtime files into a fresh final stage:
# removing files in a later final-image layer would still publish the originals.
RUN python -c 'from pathlib import Path; import shutil; root = Path("/usr/local/lib/python3.13/site-packages"); paths = sorted(root.rglob("tests"), key=lambda p: len(p.parts), reverse=True); [shutil.rmtree(p) for p in paths if p.is_dir() and not p.is_symlink()]'

FROM runtime-base
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin nodeagent && \
    mkdir -p /var/lib/performance-node && chmod 0700 /var/lib/performance-node && \
    chown nodeagent:nodeagent /var/lib/performance-node
COPY --from=build /usr/local/lib/python3.13/site-packages/ /usr/local/lib/python3.13/site-packages/
COPY --from=build /usr/local/bin/locust /usr/local/bin/locust
COPY pyproject.toml README.md ./
COPY src/performance_node/*.py ./src/performance_node/

USER nodeagent
ENV PERFORMANCE_NODE_STATE_DIR=/var/lib/performance-node
ENV PERFORMANCE_STUNNEL_BINARY=/usr/bin/stunnel4
ENTRYPOINT ["python", "-m", "performance_node"]
CMD ["run"]
