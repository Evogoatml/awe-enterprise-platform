# utils/observability.py
import uuid
import time
from contextvars import ContextVar
from typing import Dict, Any, Optional
import json

trace_id_var: ContextVar[str] = ContextVar('trace_id', default='')
span_id_var: ContextVar[str] = ContextVar('span_id', default='')

class Tracer:
    """OpenTelemetry-style distributed tracing"""
    
    def __init__(self, service_name: str, exporter=None):
        self.service_name = service_name
        self.exporter = exporter or ConsoleExporter()
        self.spans: list = []
        
    def start_trace(self, operation: str, attributes: Dict = None) -> 'Span':
        """Start new distributed trace"""
        trace_id = str(uuid.uuid4())
        trace_id_var.set(trace_id)
        
        return self._start_span(operation, trace_id, None, attributes)
    
    def start_span(self, operation: str, attributes: Dict = None) -> 'Span':
        """Start child span"""
        parent_id = span_id_var.get()
        trace_id = trace_id_var.get() or str(uuid.uuid4())
        return self._start_span(operation, trace_id, parent_id, attributes)
    
    def _start_span(self, operation: str, trace_id: str, parent_id: Optional[str], attributes: Dict):
        span = Span(
            tracer=self,
            trace_id=trace_id,
            span_id=str(uuid.uuid4())[:16],
            parent_id=parent_id,
            operation=operation,
            attributes=attributes or {},
            start_time=time.time()
        )
        span_id_var.set(span.span_id)
        return span
    
    def export_span(self, span: 'Span'):
        """Export completed span"""
        self.exporter.export({
            'trace_id': span.trace_id,
            'span_id': span.span_id,
            'parent_id': span.parent_id,
            'service': self.service_name,
            'operation': span.operation,
            'start_time': span.start_time,
            'end_time': span.end_time,
            'duration_ms': (span.end_time - span.start_time) * 1000,
            'attributes': span.attributes,
            'status': span.status,
            'error': span.error,
        })

class Span:
    def __init__(self, tracer, trace_id, span_id, parent_id, operation, attributes, start_time):
        self.tracer = tracer
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_id = parent_id
        self.operation = operation
        self.attributes = attributes
        self.start_time = start_time
        self.end_time = None
        self.status = 'ok'
        self.error = None
        
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = time.time()
        if exc_val:
            self.status = 'error'
            self.error = str(exc_val)
        self.tracer.export_span(self)
    
    def set_attribute(self, key: str, value: Any):
        self.attributes[key] = value
    
    def add_event(self, name: str, attributes: Dict = None):
        self.attributes[f'event_{name}_{time.time()}'] = attributes or {}

class ConsoleExporter:
    def export(self, span: Dict):
        print(f"[TRACE] {span['trace_id'][:8]} | {span['operation']} | {span['duration_ms']:.2f}ms | {span['status']}")

# Metrics collection
class MetricsCollector:
    """Prometheus-style metrics"""
    
    def __init__(self):
        self.counters: Dict[str, int] = {}
        self.gauges: Dict[str, float] = {}
        self.histograms: Dict[str, list] = {}
        
    def inc(self, name: str, value: int = 1, labels: Dict = None):
        key = self._key(name, labels)
        self.counters[key] = self.counters.get(key, 0) + value
        
    def gauge(self, name: str, value: float, labels: Dict = None):
        key = self._key(name, labels)
        self.gauges[key] = value
        
    def histogram(self, name: str, value: float, labels: Dict = None):
        key = self._key(name, labels)
        if key not in self.histograms:
            self.histograms[key] = []
        self.histograms[key].append(value)
        
    def _key(self, name: str, labels: Dict) -> str:
        if not labels:
            return name
        label_str = ','.join(f'{k}={v}' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"
    
    def get_stats(self) -> Dict:
        """Get current metrics snapshot"""
        stats = {
            'counters': self.counters.copy(),
            'gauges': self.gauges.copy(),
            'histograms': {
                k: {
                    'count': len(v),
                    'sum': sum(v),
                    'avg': sum(v) / len(v) if v else 0,
                    'p95': sorted(v)[int(len(v) * 0.95)] if v else 0,
                    'p99': sorted(v)[int(len(v) * 0.99)] if v else 0,
                }
                for k, v in self.histograms.items()
            }
        }
        return stats

# Global instances
tracer = Tracer("affiliate_platform")
metrics = MetricsCollector()