//go:build lambda

// Lambda entry point: the gin engine from internal/app behind the API Gateway proxy.
// Built with `-tags lambda` only (see Makefile), so `go build ./...` and `go vet` never need the Lambda modules.
package main

import (
	"context"

	"github.com/aws/aws-lambda-go/events"
	"github.com/aws/aws-lambda-go/lambda"
	ginadapter "github.com/awslabs/aws-lambda-go-api-proxy/gin"

	"github.com/{{ cookiecutter.github_owner }}/{{ cookiecutter.project_slug }}/internal/app"
)

var adapter = ginadapter.NewV2(app.New())

func handler(ctx context.Context, req events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	return adapter.ProxyWithContext(ctx, req)
}

func main() {
	lambda.Start(handler)
}
