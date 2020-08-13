FROM ubuntu:xenial as app

# System requirements.
RUN apt-get update && apt-get upgrade -qy
RUN apt-get install -qy \
	git-core \
	language-pack-en \
	python3.5 \
	python3-pip \
	python3.5-dev \
	libmysqlclient-dev \
	libssl-dev
RUN pip3 install --upgrade pip setuptools
RUN rm -rf /var/lib/apt/lists/*

# Python is Python3.
RUN ln -s /usr/bin/pip3 /usr/bin/pip
RUN ln -s /usr/bin/python3 /usr/bin/python

# Use UTF-8.
RUN locale-gen en_US.UTF-8
ENV LANG en_US.UTF-8
ENV LANGUAGE en_US:en
ENV LC_ALL en_US.UTF-8
ENV DJANGO_SETTINGS_MODULE registrar.settings.production

RUN mkdir -p /edx/app/registrar

# Expose ports.
EXPOSE 18734
EXPOSE 18735

RUN useradd -m --shell /bin/false app

# Working directory will be root of repo.
WORKDIR /edx/app/registrar

# Copy just Python requirements & install them.
COPY requirements/ /edx/app/registrar/requirements/
COPY Makefile /edx/app/registrar/
RUN make production-requirements

# Code is owned by root so it cannot be modified by the application user.
# So we copy it before changing users.
USER app

# Gunicorn 19 does not log to stdout or stderr by default. Once we are past gunicorn 19, the logging to STDOUT need not be specified.
CMD ["gunicorn", "--workers=2", "--name", "registrar", "-c", "/edx/app/registrar/registrar/docker_gunicorn_configuration.py", "--log-file", "-", "--max-requests=1000", "registrar.wsgi:application"]

# After the requirements so changes to the code will not bust the image cache
COPY . /edx/app/registrar

FROM app as newrelic
RUN pip3 install newrelic
CMD ["newrelic-admin", "run-program", "gunicorn", "--workers=2", "--name", "registrar", "-c", "/edx/app/registrar/registrar/docker_gunicorn_configuration.py", "--log-file", "-", "--max-requests=1000", "registrar.wsgi:application"]
