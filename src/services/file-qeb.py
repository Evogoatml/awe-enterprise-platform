# enterprise/circuit_breakers.py
import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Any, Set
from collections import deque
import logging

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"           # Normal operation
    OPEN = "open"               # Failing, fast reject
    HALF_OPEN = "half_open"     # Testing recovery
    FORCED_OPEN = "forced_open" # Manual shutdown

@dataclass
class CircuitMetrics:
    """Detailed circuit metrics"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    timeout_calls: int = 0
    
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    consecutive_successes: int = 0
    consecutive_failures: int = 0
    
    response_times: deque = field(default_factory=lambda: deque(maxlen=100))
    error_types: Dict[str, int] = field(default_factory=dict)
    
    @property
    def failure_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.failed_calls / self.total_calls
    
    @property
    def avg_response_time(self) -> float:
        if not self.response_times:
            return 0.0
        return sum(self.response_times) / len(self.response_times)
    
    @property
    def health_score(self) -> float:
        """0-1 health score"""
        if self.total_calls < 10:
            return 1.0  # Not enough data
        
        score = 1.0
        score -= self.failure_rate * 0.5
        score -= (self.avg_response_time / 10) * 0.3  # Penalize slow
        score -= (self.rejected_calls / max(self.total_calls, 1)) * 0.2
        
        return max(0, min(1, score))

class AdaptiveCircuitBreaker:
    """Circuit breaker with adaptive thresholds"""
    
    def __init__(self,
                 name: str,
                 failure_threshold: int = 5,
                 success_threshold: int = 3,
                 timeout_duration: float = 60.0,
                 half_open_max_calls: int = 3,
                 adaptive: bool = True):
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout_duration = timeout_duration
        self.half_open_max_calls = half_open_max_calls
        self.adaptive = adaptive
        
        self.state = CircuitState.CLOSED
        self.metrics = CircuitMetrics()
        self._lock = asyncio.Lock()
        self._half_open_calls = 0
        
        # Adaptive settings
        self.current_failure_threshold = failure_threshold
        self.current_timeout = timeout_duration
        
        # History for adaptive tuning
        self.state_history: deque = deque(maxlen=100)
        
    async def call(self, 
                   func: Callable, 
                   *args,
                   fallback: Optional[Callable] = None,
                   timeout: Optional[float] = None,
                   **kwargs) -> Any:
        """Execute with circuit breaker protection"""
        
        # Check if we should allow call
        async with self._lock:
            allowed, reason = await self._can_execute()
            if not allowed:
                self.metrics.rejected_calls += 1
                if fallback:
                    logger.info(f"Circuit {self.name} {self.state.value}, executing fallback")
                    return await fallback(*args, **kwargs)
                raise CircuitBreakerOpen(
                    f"Circuit {self.name} is {self.state.value}: {reason}"
                )
            
            if self.state == CircuitState.HALF_OPEN:
                self._half_open_calls += 1
        
        # Execute with timeout
        start_time = time.time()
        try:
            actual_timeout = timeout or self.current_timeout
            
            if asyncio.iscoroutinefunction(func):
                result = await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=actual_timeout
                )
            else:
                # Run sync function in thread pool
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, func, *args, **kwargs),
                    timeout=actual_timeout
                )
            
            elapsed = time.time() - start_time
            await self._on_success(elapsed)
            return result
            
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            await self._on_timeout(elapsed)
            if fallback:
                return await fallback(*args, **kwargs)
            raise
            
        except Exception as e:
            elapsed = time.time() - start_time
            await self._on_failure(type(e).__name__, elapsed)
            if fallback:
                return await fallback(*args, **kwargs)
            raise
    
    async def _can_execute(self) -> tuple:
        """Check if call should be allowed"""
        
        if self.state == CircuitState.CLOSED:
            return True, "normal"
        
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                self.metrics.consecutive_successes = 0
                logger.info(f"Circuit {self.name} entering HALF_OPEN")
                return True, "testing recovery"
            return False, f"cooling down for {self.current_timeout}s"
        
        if self.state == CircuitState.HALF_OPEN:
            if self._half_open_calls >= self.half_open_max_calls:
                return False, "half-open limit reached"
            return True, "probing"
        
        if self.state == CircuitState.FORCED_OPEN:
            return False, "manually disabled"
        
        return False, "unknown state"
    
    async def _on_success(self, response_time: float):
        """Handle successful call"""
        async with self._lock:
            self.metrics.total_calls += 1
            self.metrics.successful_calls += 1
            self.metrics.consecutive_successes += 1
            self.metrics.consecutive_failures = 0
            self.metrics.last_success_time = datetime.now()
            self.metrics.response_times.append(response_time)
            
            if self.state == CircuitState.HALF_OPEN:
                if self.metrics.consecutive_successes >= self.success_threshold:
                    self._close_circuit()
            
            # Adaptive: lower threshold if consistently healthy
            if self.adaptive and self.metrics.consecutive_successes > 50:
                self.current_failure_threshold = max(
                    3, self.current_failure_threshold - 1
                )
    
    async def _on_failure(self, error_type: str, response_time: float):
        """Handle failed call"""
        async with self._lock:
            self.metrics.total_calls += 1
            self.metrics.failed_calls += 1
            self.metrics.consecutive_failures += 1
            self.metrics.consecutive_successes = 0
            self.metrics.last_failure_time = datetime.now()
            self.metrics.response_times.append(response_time)
            self.metrics.error_types[error_type] = \
                self.metrics.error_types.get(error_type, 0) + 1
            
            if self.state == CircuitState.HALF_OPEN:
                # Failure in half-open, go back to open
                self._open_circuit()
            elif self.state == CircuitState.CLOSED:
                if self.metrics.consecutive_failures >= self.current_failure_threshold:
                    self._open_circuit()
            
            # Adaptive: increase threshold and timeout on repeated failures
            if self.adaptive and self.metrics.consecutive_failures > self.current_failure_threshold:
                self.current_failure_threshold = min(20, self.current_failure_threshold + 2)
                self.current_timeout = min(300, self.current_timeout * 1.5)
    
    async def _on_timeout(self, response_time: float):
        """Handle timeout"""
        async with self._lock:
            self.metrics.total_calls += 1
            self.metrics.timeout_calls += 1
            self.metrics.consecutive_failures += 1
            self.metrics.response_times.append(response_time)
            
            if self.state == CircuitState.HALF_OPEN:
                self._open_circuit()
            elif self.state == CircuitState.CLOSED:
                if self.metrics.consecutive_failures >= self.current_failure_threshold:
                    self._open_circuit()
    
    def _open_circuit(self):
        """Open the circuit"""
        self.state = CircuitState.OPEN
        self.state_history.append({
            'state': 'open',
            'timestamp': datetime.now(),
            'failure_rate': self.metrics.failure_rate
        })
        logger.warning(
            f"Circuit {self.name} OPENED - "
            f"failure rate: {self.metrics.failure_rate:.2%}, "
            f"consecutive failures: {self.metrics.consecutive_failures}"
        )
    
    def _close_circuit(self):
        """Close the circuit (recovery)"""
        self.state = CircuitState.CLOSED
        self._half_open_calls = 0
        self.metrics.consecutive_failures = 0
        self.state_history.append({
            'state': 'closed',
            'timestamp': datetime.now()
        })
        logger.info(f"Circuit {self.name} CLOSED (recovered)")
        
        # Reset adaptive params
        if self.adaptive:
            self.current_failure_threshold = self.failure_threshold
            self.current_timeout = self.timeout_duration
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time passed to try recovery"""
        if not self.metrics.last_failure_time:
            return True
        
        elapsed = (datetime.now() - self.metrics.last_failure_time).total_seconds()
        
        # Adaptive: longer timeout for repeated failures
        multiplier = 1 + (self.metrics.failed_calls // 10) * 0.5
        return elapsed >= (self.current_timeout * multiplier)
    
    def force_open(self, reason: str):
        """Manually open circuit"""
        self.state = CircuitState.FORCED_OPEN
        logger.warning(f"Circuit {self.name} manually opened: {reason}")
    
    def force_close(self):
        """Manually close circuit"""
        self._close_circuit()
        logger.info(f"Circuit {self.name} manually closed")
    
    def get_status(self) -> Dict:
        """Get current circuit status"""
        return {
            'name': self.name,
            'state': self.state.value,
            'metrics': {
                'total_calls': self.metrics.total_calls,
                'failure_rate': self.metrics.failure_rate,
                'avg_response_time': self.metrics.avg_response_time,
                'health_score': self.metrics.health_score,
                'consecutive_failures': self.metrics.consecutive_failures,
                'consecutive_successes': self.metrics.consecutive_successes
            },
            'adaptive_settings': {
                'failure_threshold': self.current_failure_threshold,
                'timeout': self.current_timeout
            },
            'state_history': list(self.state_history)[-10:]
        }

class BulkheadIsolator:
    """Bulkhead pattern - limit concurrent operations per platform"""
    
    def __init__(self, name: str, max_concurrent: int, max_queue: int = 100):
        self.name = name
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.queue_size = asyncio.Semaphore(max_queue)
        self.active_count = 0
        self.queue_count = 0
        self.metrics = {
            'total_executed': 0,
            'total_queued': 0,
            'total_rejected': 0,
            'max_wait_time': 0.0
        }
    
    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute with bulkhead constraints"""
        
        acquired_queue = False
        start_wait = time.time()
        
        try:
            # Try to acquire queue slot (non-blocking)
            acquired_queue = await asyncio.wait_for(
                self.queue_size.acquire(),
                timeout=0.1
            )
        except asyncio.TimeoutError:
            self.metrics['total_rejected'] += 1
            raise BulkheadFull(f"Bulkhead {self.name} queue full")
        
        if not acquired_queue:
            raise BulkheadFull(f"Bulkhead {self.name} queue full")
        
        self.queue_count += 1
        
        try:
            # Wait for execution slot
            async with self.semaphore:
                wait_time = time.time() - start_wait
                self.metrics['max_wait_time'] = max(
                    self.metrics['max_wait_time'], 
                    wait_time
                )
                
                self.queue_count -= 1
                self.active_count += 1
                self.metrics['total_executed'] += 1
                
                try:
                    if asyncio.iscoroutinefunction(func):
                        return await func(*args, **kwargs)
                    else:
                        return func(*args, **kwargs)
                finally:
                    self.active_count -= 1
        finally:
            self.queue_size.release()
    
    def get_status(self) -> Dict:
        return {
            'name': self.name,
            'active': self.active_count,
            'queued': self.queue_count,
            'metrics': self.metrics.copy()
        }

class CircuitBreakerManager:
    """Manage all circuit breakers"""
    
    def __init__(self):
        self.circuits: Dict[str, AdaptiveCircuitBreaker] = {}
        self.bulkheads: Dict[str, BulkheadIsolator] = {}
        
        # Platform-specific configurations
        self.configs = {
            'reddit': {
                'failure_threshold': 3,
                'timeout': 300,
                'bulkhead_max': 5,
                'adaptive': True
            },
            'twitter': {
                'failure_threshold': 5,
                'timeout': 600,
                'bulkhead_max': 10,
                'adaptive': True
            },
            'telegram': {
                'failure_threshold': 10,
                'timeout': 60,
                'bulkhead_max': 20,
                'adaptive': False
            },
            'awe_api': {
                'failure_threshold': 10,
                'timeout': 30,
                'bulkhead_max': 50,
                'adaptive': True
            },
            'fingerprinting': {
                'failure_threshold': 5,
                'timeout': 10,
                'bulkhead_max': 100,
                'adaptive': False
            }
        }
        
        # Health check task
        self._health_task = None
    
    def get_circuit(self, name: str) -> AdaptiveCircuitBreaker:
        """Get or create circuit breaker"""
        if name not in self.circuits:
            config = self.configs.get(name, {})
            self.circuits[name] = AdaptiveCircuitBreaker(
                name=name,
                failure_threshold=config.get('failure_threshold', 5),
                timeout_duration=config.get('timeout', 60),
                adaptive=config.get('adaptive', True)
            )
        return self.circuits[name]
    
    def get_bulkhead(self, name: str) -> BulkheadIsolator:
        """Get or create bulkhead"""
        if name not in self.bulkheads:
            config = self.configs.get(name, {})
            self.bulkheads[name] = BulkheadIsolator(
                name=name,
                max_concurrent=config.get('bulkhead_max', 10)
            )
        return self.bulkheads[name]
    
    async def execute(self,
                      platform: str,
                      func: Callable,
                      *args,
                      fallback: Optional[Callable] = None,
                      **kwargs) -> Any:
        """Execute with both circuit breaker and bulkhead"""
        
        circuit = self.get_circuit(platform)
        bulkhead = self.get_bulkhead(platform)
        
        # First check circuit
        async def bulkhead_wrapper():
            return await bulkhead.execute(func, *args, **kwargs)
        
        return await circuit.call(
            bulkhead_wrapper,
            fallback=fallback
        )
    
    async def start_health_monitoring(self, interval: int = 30):
        """Start background health monitoring"""
        while True:
            await self._check_health()
            await asyncio.sleep(interval)
    
    async def _check_health(self):
        """Check all circuits and alert on issues"""
        for name, circuit in self.circuits.items():
            status = circuit.get_status()
            
            if status['state'] == 'open':
                logger.error(f"ALERT: Circuit {name} is OPEN - "
                           f"failure rate: {status['metrics']['failure_rate']:.2%}")
            
            elif status['metrics']['health_score'] < 0.5:
                logger.warning(f"WARNING: Circuit {name} health score "
                             f"{status['metrics']['health_score']:.2f}")
        
        for name, bulkhead in self.bulkheads.items():
            status = bulkhead.get_status()
            if status['queued'] > 50:
                logger.warning(f"WARNING: Bulkhead {name} has "
                             f"{status['queued']} queued operations")
    
    def get_all_status(self) -> Dict:
        """Get status of all circuits and bulkheads"""
        return {
            'circuits': {
                name: cb.get_status() 
                for name, cb in self.circuits.items()
            },
            'bulkheads': {
                name: bh.get_status() 
                for name, bh in self.bulkheads.items()
            }
        }
    
    def force_open_all(self, reason: str):
        """Emergency stop"""
        for circuit in self.circuits.values():
            circuit.force_open(reason)
    
    def force_close_all(self):
        """Emergency resume"""
        for circuit in self.circuits.values():
            circuit.force_close()

class CircuitBreakerOpen(Exception):
    pass

class BulkheadFull(Exception):
    pass

# Integration with platform bots
class ResilientPlatformBot:
    """Wrapper that adds circuit breaker to any bot"""
    
    def __init__(self, bot, circuit_manager: CircuitBreakerManager):
        self.bot = bot
        self.circuits = circuit_manager
        self.platform = getattr(bot, 'platform', 'unknown')
    
    async def post(self, content: Dict) -> str:
        """Post with full resilience"""
        
        async def do_post():
            return await self.bot.post(content)
        
        async def fallback():
            # Fallback: queue for later retry
            await self._queue_for_retry(content)
            return "queued_for_retry"
        
        return await self.circuits.execute(
            self.platform,
            do_post,
            fallback=fallback
        )
    
    async def _queue_for_retry(self, content: Dict):
        """Queue failed post for later retry"""
        # Store in Redis/DB for retry worker
        pass