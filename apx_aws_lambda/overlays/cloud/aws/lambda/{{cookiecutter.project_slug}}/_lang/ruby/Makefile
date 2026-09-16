# SAM build recipe (BuildMethod: makefile): the app, its gems and the handler.
# Lambda calls the Rack app directly, so the server gems (puma, rackup — native extensions) stay out:
# a Gemfile without them is resolved for the package, and the build host needs no compiler.
# Bundler is driven by environment only — the project's own .bundle/config is never touched.

LAMBDA_BUNDLE = BUNDLE_GEMFILE=Gemfile.lambda BUNDLE_PATH=vendor/lambda BUNDLE_WITHOUT=development:test BUNDLE_FROZEN=false

build-ApiFunction:
	grep -v -E '^\s*gem "(puma|rackup)"' Gemfile > Gemfile.lambda
	$(LAMBDA_BUNDLE) bundle install --quiet
	cp -r app "$(ARTIFACTS_DIR)/app"
	cp app.rb config.ru lambda_handler.rb "$(ARTIFACTS_DIR)/"
	cp Gemfile.lambda "$(ARTIFACTS_DIR)/Gemfile"
	cp Gemfile.lambda.lock "$(ARTIFACTS_DIR)/Gemfile.lock"
	mkdir -p "$(ARTIFACTS_DIR)/vendor/bundle" "$(ARTIFACTS_DIR)/.bundle"
	cp -r vendor/lambda/. "$(ARTIFACTS_DIR)/vendor/bundle/"
	printf 'BUNDLE_PATH: "vendor/bundle"\nBUNDLE_WITHOUT: "development:test"\n' > "$(ARTIFACTS_DIR)/.bundle/config"
	rm -rf vendor/lambda Gemfile.lambda Gemfile.lambda.lock
	rmdir vendor 2>/dev/null || true
