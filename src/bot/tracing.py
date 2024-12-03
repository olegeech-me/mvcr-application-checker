import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.zipkin.json import ZipkinExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from bot.loader import TRACING_DISABLED, ZIPKIN_ENDPOINT, ZIPKIN_TIMEOUT

os.environ["OTEL_SDK_DISABLED"] = TRACING_DISABLED
os.environ["OTEL_EXPORTER_ZIPKIN_ENDPOINT"] = ZIPKIN_ENDPOINT
os.environ["OTEL_EXPORTER_ZIPKIN_TIMEOUT"] = ZIPKIN_TIMEOUT


resource = Resource(attributes={"service.name": "mvcr-telegram-bot"})

tracer_provider = TracerProvider(resource=resource)
trace.set_tracer_provider(tracer_provider)

zipkin_exporter = ZipkinExporter()

span_processor = BatchSpanProcessor(zipkin_exporter)
tracer_provider.add_span_processor(span_processor)

tracer = trace.get_tracer(__name__)
