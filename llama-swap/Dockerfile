# llama-swap with the docker CLI + compose plugin, so it can launch the
# managed services in compose.yaml through the host's docker socket (ADR 0019).
# The release binary is static (CGO_ENABLED=0), so the Alpine-based docker
# image runs it as-is. arm64 only: this image only ever runs on the Spark.
FROM docker:27-cli

ARG LS_VER=260
ARG LS_SHA256=e796fbb819d580dcccb9f86e64f7fd87044dae6fc0992a2ba6296145592aa059

RUN wget -qO /tmp/llama-swap.tar.gz \
      "https://github.com/mostlygeek/llama-swap/releases/download/v${LS_VER}/llama-swap_${LS_VER}_linux_arm64.tar.gz" && \
    echo "${LS_SHA256}  /tmp/llama-swap.tar.gz" | sha256sum -c - && \
    tar -xzf /tmp/llama-swap.tar.gz -C /usr/local/bin llama-swap && \
    rm /tmp/llama-swap.tar.gz && \
    docker compose version

ENTRYPOINT ["llama-swap"]
