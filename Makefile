.DEFAULT_GOAL := test

.PHONY: clean compile_translations dummy_translations extract_translations fake_translations help html_coverage \
	migrate pull_translations push_translations quality pii_check requirements test update_translations validate

help:
	@echo "Please use \`make <target>' where <target> is one of"
	@echo "  clean                      delete generated byte code and coverage reports"
	@echo "  compile_translations       compile translation files, outputting .po files for each supported language"
	@echo "  dummy_translations         generate dummy translation (.po) files"
	@echo "  extract_translations       extract strings to be translated, outputting .mo files"
	@echo "  fake_translations          generate and compile dummy translation files"
	@echo "  help                       display this help message"
	@echo "  html_coverage              generate and view HTML coverage report"
	@echo "  migrate                    apply database migrations"
	@echo "  prod-requirements          install requirements for production"
	@echo "  pull_translations          pull translations from Transifex"
	@echo "  push_translations          push source translation files (.po) from Transifex"
	@echo "  quality                    run Pycodestyle and Pylint"
	@echo "  pii_check                  check for PII annotations on all Django models"
	@echo "  requirements               install requirements for local development"
	@echo "  test                       run tests and generate coverage report"
	@echo "  validate                   run tests and quality checks"
	@echo "  start-devstack             run a local development copy of the server"
	@echo "  open-devstack              open a shell on the server started by start-devstack"
	@echo "  pkg-devstack               build the registrar image from the latest configuration and code"
	@echo "  detect_changed_source_translations       check if translation files are up-to-date"
	@echo "  validate_translations      install fake translations and check if translation files are up-to-date"
	@echo ""

clean:
	find . -name '*.pyc' -delete
	coverage erase
	rm -rf assets
	rm -rf pii_report

pkg-devstack:
	docker build -t registrar:latest -f docker/build/registrar/Dockerfile git://github.com/edx/configuration

registrar-app.env:
	cp registrar-app.env.template registrar-app.env

build: registrar-app.env
	docker-compose build

up:
	docker-compose up -d

down:
	docker-compose down

destroy:
	docker-compose down --volumes

shell:
	docker exec -it registrar-app /bin/bash

db_shell:
	docker exec -it registrar-db mysql -uroot registrar

logs:
	docker logs -f registrar-app

provision: provision_db restart update_db create_superuser

provision_db:
	@echo -n 'Waiting for database... '
	@sleep 10
	@echo 'done.'
	docker exec -i registrar-db mysql -uroot mysql < provision.sql

restart: ## Kill the Django development server. The watcher process will restart it.
	docker exec -t registrar-app bash -c 'kill $$(ps aux | grep "manage.py runserver" | egrep -v "while|grep" | awk "{print \$$2}")'

update_db:
	docker exec -t registrar-app bash -c 'python manage.py migrate'

create_superuser:
	docker exec -t registrar-app bash -c 'echo "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.create_superuser(\"edx\", \"edx@example.com\",\"edx\")" | python manage.py shell'


# The followeing targets must be build from within the Docker container,
# which can be accessed using `make shell` after running `make up`.

upgrade: piptools
	pip-compile --upgrade -o requirements/production.txt requirements/production.in
	pip-compile --upgrade -o requirements/local.txt requirements/local.in
	pip-compile --upgrade -o requirements/test.txt requirements/test.in
	pip-compile --upgrade -o requirements/monitoring/requirements.txt requirements/monitoring/requirements.in

piptools:
	pip install -q pip-tools

requirements:
	pip-sync -q requirements/local.txt

prod-requirements:
	pip-sync -q requirements.txt

test: clean
	coverage run ./manage.py test registrar --settings=registrar.settings.test
	coverage report

quality:
	pycodestyle registrar *.py
	pylint --rcfile=pylintrc registrar *.py

pii_check:
	DJANGO_SETTINGS_MODULE=registrar.settings.test \
	code_annotations django_find_annotations --config_file .pii_annotations.yml --lint --report --coverage

validate: test quality pii_check

migrate:
	python manage.py migrate

html_coverage:
	coverage html && open htmlcov/index.html

extract_translations:
	python manage.py makemessages -l en -v1 -d django
	python manage.py makemessages -l en -v1 -d djangojs

dummy_translations:
	cd registrar && i18n_tool dummy

compile_translations:
	python manage.py compilemessages

fake_translations: extract_translations dummy_translations compile_translations

pull_translations:
	tx pull -af --mode reviewed

push_translations:
	tx push -s

detect_changed_source_translations:
	cd registrar && i18n_tool changed

validate_translations: fake_translations detect_changed_source_translations
