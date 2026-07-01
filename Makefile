# Makefile para o projeto Port Map

MIN_COVERAGE = 50

.PHONY: help
help:
	@echo "Comandos disponiveis:"
	@echo "  make install        - Instala dependencias e configura hooks do pre-commit"
	@echo "  make format         - Formata o codigo usando black e isort"
	@echo "  make lint           - Roda o linter flake8"
	@echo "  make test           - Roda todos os testes unitarios"
	@echo "  make test-coverage  - Executa testes e valida cobertura minima de $(MIN_COVERAGE)%"
	@echo "  make pre-commit     - Executa pre-commit em todos os arquivos"

.PHONY: install
install:
	poetry install
	poetry run pre-commit install

.PHONY: format
format:
	poetry run black .
	poetry run isort .

.PHONY: lint
lint:
	poetry run flake8 .

.PHONY: test
test:
	poetry run python manage.py test --settings=app.test

.PHONY: test-coverage
test-coverage:
	poetry run coverage erase
	poetry run coverage run manage.py test --settings=app.test
	poetry run coverage report -m --fail-under=$(MIN_COVERAGE)
	poetry run coverage html
	@echo "Relatorio HTML de cobertura disponivel em htmlcov/index.html"

.PHONY: pre-commit
pre-commit:
	poetry run pre-commit run --all-files
