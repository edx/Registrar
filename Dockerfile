FROM ubuntu:xenial

RUN apt-get update && \
  apt-get install -qy git-core language-pack-en libmysqlclient-dev libssl-dev python3.5 python3-pip python3.5-dev && \
  pip3 install --upgrade pip setuptools && \
  rm -rf /var/lib/apt/lists/*

RUN ln -s /usr/bin/pip3 /usr/bin/pip
RUN ln -s /usr/bin/python3 /usr/bin/python

RUN locale-gen en_US.UTF-8
ENV LANG en_US.UTF-8
ENV LANGUAGE en_US:en
ENV LC_ALL en_US.UTF-8

RUN mkdir -p /edx/app/registrar

WORKDIR /edx/app/registrar
COPY requirements /edx/app/registrar/requirements
RUN pip install -r requirements/production.txt

RUN useradd -m --shell /bin/false app
USER app

EXPOSE 8734
CMD gunicorn -c /edx/app/registrar/registrar/docker_gunicorn_configuration.py --bind=0.0.0.0:8734 --workers 2 --max-requests=1000 registrar.wsgi:application

COPY . /edx/app/registrar
