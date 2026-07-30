FROM alpine:3.24

ARG CRYPTOGRAPHY_DONT_BUILD_RUST=1

RUN apk --no-cache add \
        sudo \
        python3 \
        py3-pip \
        openssl \
        ca-certificates \
        sshpass \
        openssh-client \
        rsync \
        git && \
    apk --no-cache add --virtual build-dependencies \
        python3-dev \
        libffi-dev \
        musl-dev \
        gcc \
        cargo \
        openssl-dev \
        libressl-dev \
        build-base && \
    pip3 install --upgrade pip wheel && \
    pip3 install --upgrade cryptography cffi && \
    pip3 install ansible==2.10.7 && \
    pip3 install mitogen ansible-lint jmespath && \
    pip3 install --upgrade pywinrm && \
    apk del build-dependencies && \
    rm -rf /var/cache/apk/* && \
    rm -rf /root/.cache/pip && \
    rm -rf /root/.cargo

# Create non-root user for ansible operations
RUN addgroup -S ansible && adduser -S ansible -G ansible

RUN mkdir /ansible && \
    mkdir -p /etc/ansible && \
    echo 'localhost' > /etc/ansible/hosts && \
    chown -R ansible:ansible /ansible

WORKDIR /ansible

USER ansible

HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD ansible --version || exit 1

CMD [ "ansible-playbook", "--version" ]
