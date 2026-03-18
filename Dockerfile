FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    container=docker \
    BUN_INSTALL=/root/.bun \
    PATH=/root/.bun/bin:$PATH

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
    gnupg \
    iproute2 \
    iputils-ping \
    jq \
    less \
    nginx \
    openssh-server \
    postgresql-client \
    procps \
    python3 \
    python3-certbot-nginx \
    shellcheck \
    sudo \
    systemd \
    systemd-sysv \
    ufw \
    unzip \
    vim-tiny \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
 && printf '%s\n' \
    'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main' \
    > /etc/apt/sources.list.d/nodesource.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
    nodejs \
 && curl -fsSL https://bun.sh/install | bash \
 && rm -rf /var/lib/apt/lists/*

COPY . /opt/server-setup

RUN chmod +x \
    /opt/server-setup/scripts/*.sh \
    /opt/server-setup/tests/*.sh \
    /opt/server-setup/benchmarks/*.sh \
 && mkdir -p \
    /etc/systemd/system/multi-user.target.wants \
    /etc/nginx/sites-available \
    /etc/nginx/sites-enabled \
    /run/sshd \
    /srv/apps \
    /srv/github-sites \
    /var/www \
 && install -m 0644 \
    /opt/server-setup/ops/nginx/simple-site-direct.conf \
    /etc/nginx/sites-available/simple-site-direct.conf \
 && ln -sf \
    /etc/nginx/sites-available/simple-site-direct.conf \
    /etc/nginx/sites-enabled/simple-site-direct.conf \
 && install -m 0644 \
    /opt/server-setup/ops/nginx/server-setup-status-webapp.conf \
    /etc/nginx/sites-available/server-setup-status-webapp.conf \
 && ln -sf \
    /etc/nginx/sites-available/server-setup-status-webapp.conf \
    /etc/nginx/sites-enabled/server-setup-status-webapp.conf \
 && install -m 0644 \
    /opt/server-setup/ops/systemd/server-setup-example-apps.service \
    /etc/systemd/system/server-setup-example-apps.service \
 && ln -sf \
    /etc/systemd/system/server-setup-example-apps.service \
    /etc/systemd/system/multi-user.target.wants/server-setup-example-apps.service \
 && install -m 0644 \
    /opt/server-setup/ops/systemd/server-setup-status-webapp.service \
    /etc/systemd/system/server-setup-status-webapp.service \
 && ln -sf \
    /etc/systemd/system/server-setup-status-webapp.service \
    /etc/systemd/system/multi-user.target.wants/server-setup-status-webapp.service \
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

EXPOSE 22 80 443 4000 4001 4002 4003

STOPSIGNAL SIGRTMIN+3

ENTRYPOINT ["/opt/server-setup/scripts/sandbox-entrypoint.sh"]

CMD ["/sbin/init"]
