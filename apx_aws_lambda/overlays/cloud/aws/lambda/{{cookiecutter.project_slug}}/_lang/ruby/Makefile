# SAM build recipe (BuildMethod: makefile): the app, its gems and the handler.
# Lambda calls the Rack app directly, so the server gems (puma, rackup — native extensions) stay out:
# a Gemfile without them is resolved for the package, and the build host needs no compiler.

build-ApiFunction:
	grep -v -E '^\s*gem "(puma|rackup)"' Gemfile > Gemfile.lambda
	BUNDLE_GEMFILE=Gemfile.lambda bundle config set --local path vendor/bundle
	BUNDLE_GEMFILE=Gemfile.lambda bundle config set --local without development:test
	BUNDLE_GEMFILE=Gemfile.lambda bundle install --quiet
	cp -r app "$(ARTIFACTS_DIR)/app"
	cp app.rb config.ru lambda_handler.rb "$(ARTIFACTS_DIR)/"
	cp Gemfile.lambda "$(ARTIFACTS_DIR)/Gemfile"
	cp Gemfile.lambda.lock "$(ARTIFACTS_DIR)/Gemfile.lock"
	cp -r vendor "$(ARTIFACTS_DIR)/vendor"
	cp -r .bundle "$(ARTIFACTS_DIR)/.bundle"
