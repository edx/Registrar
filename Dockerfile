FROM ubuntu:xenial as app

# System requirements.
RUN apt-get update && apt-get upgrade -qy && \
apt-get install -y software-properties-common && \
apt-add-repository -y ppa:deadsnakes/ppa && apt-get update && apt-get upgrade -qy
RUN apt-get install -qy \
	git-core \
	language-pack-en \
	build-essential \
	python3.8-dev \
	python3.8-venv \
	libmysqlclient-dev \
	libssl-dev
RUN rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/edx/app/registrar/venvs/registrar
RUN python3.8 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"


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

RUN pip install -r requirements/pip.txt
RUN make production-requirements

USER app

# After the requirements so changes to the code will not bust the image cache
COPY . /edx/app/registrar

FROM app as newrelic
RUN pip install newrelic
CMD ["newrelic-admin", "run-program", "gunicorn", "--workers=2", "--name", "registrar", "-c", "/edx/app/registrar/registrar/docker_gunicorn_configuration.py", "--log-file", "-", "--max-requests=1000", "registrar.wsgi:application"]


FROM app as devstack
USER root
RUN make devstack-requirements
USER app
# Gunicorn 19 does not log to stdout or stderr by default. Once we are past gunicorn 19, the logging to STDOUT need not be specified.
CMD ["gunicorn", "--workers=2", "--name", "registrar", "-c", "/edx/app/registrar/registrar/docker_gunicorn_configuration.py", "--log-file", "-", "--max-requests=1000", "registrar.wsgi:application"]
