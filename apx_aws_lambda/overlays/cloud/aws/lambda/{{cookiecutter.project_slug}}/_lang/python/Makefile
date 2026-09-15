# SAM build recipes (BuildMethod: makefile). Deps go to a layer, code to the function.
# The layer is built for the function's platform (arm64, python3.12) from wheels only,
# so the build host — a laptop, CI, the platform's worker — does not matter.

LAYER_PLATFORM ?= manylinux2014_aarch64
LAYER_PYTHON ?= 3.12

build-ApiFunction:
	cp -r app "$(ARTIFACTS_DIR)/app"
	[ -d tools ] && cp -r tools "$(ARTIFACTS_DIR)/tools" || true
	cp lambda_handler.py "$(ARTIFACTS_DIR)/lambda_handler.py"

build-DepsLayer:
	mkdir -p "$(ARTIFACTS_DIR)/python"
	python -m pip install --quiet --upgrade pip >/dev/null
	python tools/lambda_requirements.py "$(ARTIFACTS_DIR)/requirements.txt"
	python -m pip install --quiet --target "$(ARTIFACTS_DIR)/python" \
		--platform "$(LAYER_PLATFORM)" --python-version "$(LAYER_PYTHON)" \
		--implementation cp --only-binary=:all: \
		mangum -r "$(ARTIFACTS_DIR)/requirements.txt"
	rm -f "$(ARTIFACTS_DIR)/requirements.txt"
