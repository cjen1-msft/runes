FROM mcr.microsoft.com/azurelinux/base/core:3.0

COPY ./bin /tools

RUN tdnf install -y ca-certificates vim tmux git curl wget python3 python3-pip
RUN python3 -m pip install httpx

COPY scripts /scripts