ARG IN_PATH

FROM ubuntu:20.04

RUN sudo apt update && sudo apt install -y openssh-server
COPY ${IN_PATH} /src
