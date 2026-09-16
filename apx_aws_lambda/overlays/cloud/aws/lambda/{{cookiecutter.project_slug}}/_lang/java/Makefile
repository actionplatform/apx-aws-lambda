# SAM build recipe (BuildMethod: makefile): the Spring Boot jar and run.sh — the Lambda Web Adapter layer starts run.sh and proxies HTTP to port 8080.

build-ApiFunction:
	mvn -q -B -DskipTests package
	cp target/*.jar "$(ARTIFACTS_DIR)/app.jar"
	cp run.sh "$(ARTIFACTS_DIR)/run.sh"
	chmod +x "$(ARTIFACTS_DIR)/run.sh"
