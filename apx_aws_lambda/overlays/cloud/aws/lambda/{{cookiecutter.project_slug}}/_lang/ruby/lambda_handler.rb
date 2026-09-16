# frozen_string_literal: true

# Lambda entry point: the Rack app from app.rb called with the request API Gateway (HTTP API, payload v2) delivers.
# No adapter gem and no server: the event becomes a Rack env, the triplet becomes the response.

require "base64"
require "rack"
require "stringio"

require_relative "app"

APP = App.create

def handler(event:, context:)
  http = event.fetch("requestContext").fetch("http")
  body = event["body"].to_s
  body = Base64.decode64(body) if event["isBase64Encoded"]
  headers = (event["headers"] || {}).transform_keys { |k| k.to_s.downcase }

  env = {
    "REQUEST_METHOD" => http["method"],
    "SCRIPT_NAME" => "",
    "PATH_INFO" => event["rawPath"] || "/",
    "QUERY_STRING" => event["rawQueryString"].to_s,
    "SERVER_NAME" => headers["host"] || "localhost",
    "SERVER_PORT" => "443",
    "SERVER_PROTOCOL" => "HTTP/1.1",
    "rack.version" => Rack::VERSION,
    "rack.url_scheme" => "https",
    "rack.input" => StringIO.new(body),
    "rack.errors" => $stderr,
    "rack.multithread" => false,
    "rack.multiprocess" => false,
    "rack.run_once" => false,
    "lambda.event" => event,
    "lambda.context" => context
  }
  headers.each do |name, value|
    key = name.upcase.tr("-", "_")
    env[%w[content-type content-length].include?(name) ? key : "HTTP_#{key}"] = value
  end

  status, response_headers, response_body = APP.call(env)
  chunks = []
  response_body.each { |chunk| chunks << chunk }
  response_body.close if response_body.respond_to?(:close)

  {
    "statusCode" => status,
    "headers" => response_headers.to_h.transform_values(&:to_s),
    "body" => chunks.join,
    "isBase64Encoded" => false
  }
end
