FROM mcr.microsoft.com/azurelinux/base/core:3.0

COPY ./bin /sidecar-tools

RUN tdnf install -y ca-certificates vim tmux git curl wget

RUN wget https://github.com/microsoft/CCF/releases/download/ccf-6.0.1/ccf_snp_devel_6.0.1_x86_64.rpm

RUN tdnf install -y /ccf_snp_devel_6.0.1_x86_64.rpm

RUN /opt/ccf_snp/bin/sandbox.sh || true

CMD ["tail", "-f", "/dev/null"]
