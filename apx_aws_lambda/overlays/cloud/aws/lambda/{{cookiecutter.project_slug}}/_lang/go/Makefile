# SAM build recipe (BuildMethod: makefile): one static binary named bootstrap for provided.al2023 on arm64.
# The Lambda modules are fetched at build time, so go.mod stays as the project template left it.

build-ApiFunction:
	go get github.com/aws/aws-lambda-go@latest github.com/awslabs/aws-lambda-go-api-proxy@latest
	GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -tags lambda -ldflags="-s -w" -o "$(ARTIFACTS_DIR)/bootstrap" ./cmd/lambda
