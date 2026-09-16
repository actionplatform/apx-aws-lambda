# frozen_string_literal: true

# Lambda entry point: the Rack app from app.rb, called with the request API Gateway
# (HTTP API, payload v2) delivers. No adapter gem, no server: the event becomes a
# Rack env, the triplet becomes the response.

require "bundler/setup"

require "base64"
require "rack"
require "stringio"

require_relative "app"

APP = App.create
BODY_HEADERS = %w[content-type content-length].freeze

def handler(event:, context:)
  status, headers, body = APP.call(rack_env(event, context))
  chunks = body.map(&:to_s)
  body.close if body.respond_to?(:close)

  {
    "statusCode" => status,
    "headers" => headers.to_h.transform_values(&:to_s),
    "body" => chunks.join,
    "isBase64Encoded" => false
  }
end

def rack_env(event, context)
  headers = (event["headers"] || {}).transform_keys { |k| k.to_s.downcase }
  env = base_env(event, headers, context)
  headers.each { |name, value| env[header_key(name)] = value }
  env
end

RACK_STATIC = {
  "SCRIPT_NAME" => "",
  "SERVER_PORT" => "443",
  "SERVER_PROTOCOL" => "HTTP/1.1",
  "rack.version" => Rack::VERSION,
  "rack.url_scheme" => "https",
  "rack.errors" => $stderr,
  "rack.multithread" => false,
  "rack.multiprocess" => false,
  "rack.run_once" => false
}.freeze

def base_env(event, headers, context)
  RACK_STATIC.merge(
    "REQUEST_METHOD" => event.fetch("requestContext").fetch("http")["method"],
    "PATH_INFO" => event["rawPath"] || "/",
    "QUERY_STRING" => event["rawQueryString"].to_s,
    "SERVER_NAME" => headers["host"] || "localhost",
    "rack.input" => StringIO.new(request_body(event)),
    "lambda.event" => event,
    "lambda.context" => context
  )
end

def request_body(event)
  body = event["body"].to_s
  event["isBase64Encoded"] ? Base64.decode64(body) : body
end

def header_key(name)
  key = name.upcase.tr("-", "_")
  BODY_HEADERS.include?(name) ? key : "HTTP_#{key}"
end
