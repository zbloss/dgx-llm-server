FROM python:3.12-slim

RUN pip install --no-cache-dir uv

RUN uv pip install --system --no-cache "guidellm[recommended]"

ENTRYPOINT ["guidellm"]
