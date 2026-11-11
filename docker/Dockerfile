# Two images, each with its own --target, built from the repo root: proxy, the only one with a way out, with
# nothing third-party; core, letsfly and curl-cffi, with no way out of its own. Bases are pinned by digest and
# dependencies by the hash of every file, wheels only.

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS proxy
COPY docker/proxy.py /proxy.py
USER 65534:65534
ENTRYPOINT ["python3", "-I", "-u", "/proxy.py"]

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /bin/uv
COPY docker/requirements.txt /tmp/
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
# --no-deps: curl-cffi declares rich, which it never imports
RUN uv venv /app/.venv && VIRTUAL_ENV=/app/.venv uv pip install --no-cache --no-deps --require-hashes \
    --only-binary :all: -r /tmp/requirements.txt
# letsfly goes straight into site-packages: that is where python -I finds it, since it ignores PYTHONPATH
COPY src/letsfly /app/.venv/lib/python3.12/site-packages/letsfly
RUN /app/.venv/bin/python -m compileall -q /app/.venv

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS core
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp
USER 65534:65534
CMD ["python", "-I", "-m", "letsfly.tools"]
