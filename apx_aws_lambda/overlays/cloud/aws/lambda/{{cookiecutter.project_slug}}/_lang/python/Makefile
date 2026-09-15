# SAM build recipes (BuildMethod: makefile). Deps go to a layer, code to the function.

build-ApiFunction:
	cp -r app "$(ARTIFACTS_DIR)/app"
	[ -d tools ] && cp -r tools "$(ARTIFACTS_DIR)/tools" || true
	cp lambda_handler.py "$(ARTIFACTS_DIR)/lambda_handler.py"

build-DepsLayer:
	mkdir -p "$(ARTIFACTS_DIR)/python"
	poetry export --without dev,code-quality --without-hashes -f requirements.txt -o /tmp/requirements.txt
	pip install mangum -r /tmp/requirements.txt -t "$(ARTIFACTS_DIR)/python"
