.PHONY: test lint python-test webapp-test docker-sandbox-test

test:
	./tests/run-tests.sh

lint:
	./tests/run-lint.sh

python-test:
	./tests/run-python-tests.sh

webapp-test:
	./tests/test_status_webapp_frontend.sh

docker-sandbox-test:
	./tests/test_docker_sandbox.sh
