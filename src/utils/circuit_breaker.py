# utils/circuit_breaker.py
from enum import Enum
import asyncio
from datetime import datetime, timedelta
from typing import Callable, Optional
import logging

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if recovered

class CircuitBreaker:
    """Prevents cascade failures when platforms ban/rate-limit"""
    
    def __init__(self, 
                 name: str,
                 failure_threshold: int = 5,
                 recovery_timeout: int = 60,
                 half_open_max_calls: int = 3):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.half_open_calls = 0
        
        self._lock = asyncio.Lock()
        
    async def call(self, func: Callable, *args, **kwargs):
        """Execute with circuit breaker protection"""
        
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    logging.info(f"Circuit {self.name} entering HALF_OPEN")
                else:
                    raise CircuitBreakerOpen(f"Circuit {self.name} is OPEN")
            
            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_calls >= self.half_open_max_calls:
                    raise CircuitBreakerOpen(f"Circuit {self.name} HALF_OPEN limit reached")
                self.half_open_calls += 1
        
        # Execute
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as e:
            await self._on_failure()
            raise
    
    async def _on_success(self):
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.half_open_max_calls:
                    self._reset()
            else:
                self.failure_count = 0
    
    async def _on_failure(self):
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now()
            
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logging.warning(f"Circuit {self.name} OPENED due to {self.failure_count} failures")
    
    def _should_attempt_reset(self) -> bool:
        if not self.last_failure_time:
            return True
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout
    
    def _reset(self):
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.half_open_calls = 0
        logging.info(f"Circuit {self.name} CLOSED (recovered)")

class PlatformCircuitBreakers:
    """Manage breakers for all platforms"""
    
    def __init__(self):
        self.breakers = {
            'reddit': CircuitBreaker("reddit", failure_threshold=3, recovery_timeout=300),
            'twitter': CircuitBreaker("twitter", failure_threshold=5, recovery_timeout=600),
            'telegram': CircuitBreaker("telegram", failure_threshold=10, recovery_timeout=60),
            'awe_api': CircuitBreaker("awe_api", failure_threshold=10, recovery_timeout=30),
        }
    
    async def execute(self, platform: str, func: Callable, *args, **kwargs):
        """Execute with platform's circuit breaker"""
        breaker = self.breakers.get(platform)
        if not breaker:
            return await func(*args, **kwargs)
        return await breaker.call(func, *args, **kwargs)