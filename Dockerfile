FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    container=docker

WORKDIR /opt/server-setup

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    certbot \
    curl \
    dbus \
    fail2ban \
    git \
    iproute2 \
    iputils-ping \
    jq \
    less \
    nginx \
    nodejs \
    npm \
    openssh-server \
    postgresql-client \
    procps \
    python3 \
    python3-certbot-nginx \
    sudo \
    systemd \
    systemd-sysv \
    ufw \
    vim-tiny \
 && rm -rf /var/lib/apt/lists/*

COPY . /opt/server-setup

RUN chmod +x \
    /opt/server-setup/scripts/*.sh \
    /opt/server-setup/tests/*.sh \
    /opt/server-setup/benchmarks/*.sh \
 && mkdir -p \
    /run/sshd \
    /srv/apps \
    /srv/github-sites \
    /var/www \
 && printf '%s\n' \
    'server-setup sandbox container' \
    '' \
    'Repository: /opt/server-setup' \
    'Seeded example repos: /srv/apps/simple-site, /srv/apps/rest-api, /srv/apps/complex-site' \
    'Use a privileged run command with /sys/fs/cgroup mounted.' \
    'Example:' \
    '  docker run --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw ...' \
    > /etc/motd

VOLUME ["/sys/fs/cgroup"]

EXPOSE 22 80 443

STOPSIGNAL SIGRTMIN+3

ENTRYPOINT ["/opt/server-setup/scripts/sandbox-entrypoint.sh"]

CMD ["/sbin/init"]
