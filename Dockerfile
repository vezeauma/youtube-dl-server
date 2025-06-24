#
# youtube-dl-server Dockerfile
#
# https://github.com/manbearwiz/youtube-dl-server/blob/main/Dockerfile
#

FROM python:3.12-alpine
ENV USER_ID=1000
ENV GROUP_ID=1000
ENV USER_NAME=python-user
ENV GROUP_NAME=python-user
ENV PATH=/home/${USER_NAME}/.local/bin:${PATH}

RUN addgroup -g $GROUP_ID $GROUP_NAME && \
  adduser --shell /sbin/nologin --disabled-password \
  --uid $USER_ID -G wheel --ingroup $GROUP_NAME $USER_NAME

RUN apk add --no-cache ffmpeg x264 ffmpeg-libs tzdata sudo \ 
  && apk --update-cache add --virtual build-dependencies gcc libc-dev make \
  && apk del build-dependencies

RUN apk add --no-cache --update curl unzip \
  && curl -L -o /tmp/AtomicParsleyAlpine.zip https://github.com/wez/atomicparsley/releases/download/20240608.083822.1ed9031/AtomicParsleyAlpine.zip \
  && unzip /tmp/AtomicParsleyAlpine.zip -d /usr/bin \
  && chmod +x /usr/bin/AtomicParsley \
  && rm /tmp/AtomicParsleyAlpine.zip \
  && apk del curl unzip


# Step 1 - install requirement HandBrake
RUN apk add --no-cache --update \
  jansson-dev \
  speex-dev \
  libjpeg-turbo-dev && \
  apk add --no-cache --update --virtual .build-deps \
  autoconf \
  automake \
  build-base \
  cmake \
  git \
  libass-dev \
  bzip2-dev \
  fontconfig-dev \
  freetype-dev \
  fribidi-dev \
  harfbuzz-dev \
  xz-dev \
  lame-dev \
  numactl-dev \
  libogg-dev \
  opus-dev \
  libsamplerate-dev \
  libtheora-dev \
  libtool \
  libvorbis-dev \
  x264-dev \
  libxml2-dev \
  libvpx-dev \
  m4 \
  meson \
  nasm \
  ninja \
  patch \
  python3 \
  py3-pip \
  pkgconf \
  tar \
  zlib-dev \
  libva-dev \
  libdrm-dev \
# Step 2 : Clone and compile HandBrake
  && git clone https://github.com/HandBrake/HandBrake.git /tmp/HandBrake \
  && cd /tmp/HandBrake \
  && ./configure --launch-jobs=$(nproc) --launch --disable-gtk \
  && make -C build install \
# Step 3 : Cleanup
  && apk del .build-deps \
  && rm -rf /tmp/HandBrake

RUN mkdir -p /usr/src/app && \
  mkdir -p /tmp/youtube-dl && \
  chown $USER_NAME:$GROUP_NAME /tmp/youtube-dl -R

COPY --chown=$USER_NAME:$GROUP_NAME templates/ /usr/src/app/templates/
COPY --chown=$USER_NAME:$GROUP_NAME youtube-dl-server.py /usr/src/app/
COPY --chown=$USER_NAME:$GROUP_NAME youtube-dl-server.png /usr/src/app/
COPY --chown=$USER_NAME:$GROUP_NAME .env /usr/src/app/

USER $USER_NAME

WORKDIR /usr/src/app

COPY requirements.txt /usr/src/app/

RUN pip install --no-cache-dir -r requirements.txt 

EXPOSE 8080

VOLUME ["/youtube-dl"]

CMD ["uvicorn", "youtube-dl-server:app", "--host", "0.0.0.0", "--port", "8080"]
