# Calculation sandbox image (contracts/sandbox.md, research §R8). One container per batch,
# started by sandbox.service with no network, a read-only root, 512 MiB and user 65534.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY sandbox/requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes --only-binary=:all: -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && pip uninstall -y pip setuptools wheel 2>/dev/null || true

COPY --chown=root:root --chmod=0444 sandbox/runner/run.py /runner/run.py
RUN chmod 0555 /runner

USER 65534:65534
WORKDIR /tmp
ENTRYPOINT []
CMD ["python", "-I", "/runner/run.py"]
