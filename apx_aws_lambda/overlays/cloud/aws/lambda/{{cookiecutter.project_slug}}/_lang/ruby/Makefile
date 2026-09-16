# SAM build recipe (BuildMethod: makefile): the app, its gems (without the development and test groups) and the handler.
# Only pure-Ruby gems are loaded on Lambda (rack, sinatra); puma stays in the Gemfile for local runs and is never required there.

build-ApiFunction:
	bundle config set --local path vendor/bundle
	bundle config set --local without development:test
	bundle install --quiet
	cp -r app "$(ARTIFACTS_DIR)/app"
	cp app.rb config.ru Gemfile Gemfile.lock lambda_handler.rb "$(ARTIFACTS_DIR)/"
	cp -r vendor "$(ARTIFACTS_DIR)/vendor"
	cp -r .bundle "$(ARTIFACTS_DIR)/.bundle"
