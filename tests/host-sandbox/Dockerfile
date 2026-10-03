ARG BASE_IMAGE=debian:12
FROM ${BASE_IMAGE}

ENV container=docker
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends systemd systemd-sysv dbus python3 openssh-server ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && systemctl enable ssh

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
