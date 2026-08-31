FROM ubuntu:24.04@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        git \
        jq \
        openssh-server \
        python3 \
        python3-pip \
        python3-venv \
        rsync \
        unzip \
        util-linux \
        wget \
        zip \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 11000 --user-group --home-dir /workspace --shell /bin/bash --password '*' ai \
    && mkdir -p /run/sshd /workspace

COPY pyproject.toml /opt/paddock/
COPY LICENSE /opt/paddock/
COPY paddock/ /opt/paddock/paddock/
RUN python3 -m pip install --break-system-packages --no-cache-dir /opt/paddock

COPY config/sshd_config /etc/ssh/sshd_config
COPY docker/entrypoint.sh /usr/local/bin/paddock-entrypoint
COPY docker/api-entrypoint.sh /usr/local/bin/paddock-api-entrypoint
COPY docker/verify-workspace.sh /usr/local/bin/paddock-verify-workspace

RUN chmod 0755 \
        /usr/local/bin/paddock-entrypoint \
        /usr/local/bin/paddock-api-entrypoint \
        /usr/local/bin/paddock-verify-workspace

EXPOSE 22 8000

ENTRYPOINT ["/usr/local/bin/paddock-entrypoint"]
