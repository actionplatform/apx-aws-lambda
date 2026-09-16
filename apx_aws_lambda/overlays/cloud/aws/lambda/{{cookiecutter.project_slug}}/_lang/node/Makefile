# SAM build recipe (BuildMethod: makefile): the compiled app, its production dependencies and run.sh — the Lambda Web Adapter layer starts run.sh and proxies HTTP to port 8080.

build-ApiFunction:
	[ -f package-lock.json ] && npm ci --no-audit --no-fund || npm install --no-audit --no-fund
	npm run build
	npm prune --omit=dev --no-audit --no-fund
	cp -r dist "$(ARTIFACTS_DIR)/dist"
	cp -r node_modules "$(ARTIFACTS_DIR)/node_modules"
	cp package.json "$(ARTIFACTS_DIR)/package.json"
	cp run.sh "$(ARTIFACTS_DIR)/run.sh"
	chmod +x "$(ARTIFACTS_DIR)/run.sh"
