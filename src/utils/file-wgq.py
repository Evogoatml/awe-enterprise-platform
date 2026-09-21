# utils/anomaly_detection.py
import numpy as np
from collections import deque
from typing import Dict, List
from datetime import datetime

class AnomalyDetector:
    """Detect unusual patterns indicating bans, blocks, or bot detection"""
    
    def __init__(self, window_size: int = 100):
        self.windows: Dict[str, deque] = {}
        self.window_size = window_size
        self.thresholds = {
            'error_rate': 0.30,      # 30% errors
            'response_time': 5.0,     # 5 seconds
            'success_rate_drop': 0.5, # 50% drop
        }
        
    def record_metric(self, platform: str, metric_type: str, value: float):
        """Record metric for platform"""
        key = f"{platform}:{metric_type}"
        
        if key not in self.windows:
            self.windows[key] = deque(maxlen=self.window_size)
        
        self.windows[key].append({
            'timestamp': datetime.now(),
            'value': value
        })
        
    def check_anomaly(self, platform: str) -> List[Dict]:
        """Check for anomalies on platform"""
        anomalies = []
        
        # Check error rate
        error_key = f"{platform}:error"
        if error_key in self.windows:
            errors = [m for m in self.windows[error_key] 
                     if m['value'] > 0]
            error_rate = len(errors) / len(self.windows[error_key])
            
            if error_rate > self.thresholds['error_rate']:
                anomalies.append({
                    'type': 'high_error_rate',
                    'platform': platform,
                    'value': error_rate,
                    'threshold': self.thresholds['error_rate'],
                    'severity': 'critical' if error_rate > 0.5 else 'warning'
                })
        
        # Check response time spike
        rt_key = f"{platform}:response_time"
        if rt_key in self.windows and len(self.windows[rt_key]) > 10:
            times = [m['value'] for m in self.windows[rt_key]]
            avg_time = np.mean(times[:-10])  # Historical avg
            recent_avg = np.mean(times[-10:])  # Recent avg
            
            if recent_avg > avg_time * 2:  # Doubled
                anomalies.append({
                    'type': 'response_time_spike',
                    'platform': platform,
                    'value': recent_avg,
                    'baseline': avg_time,
                    'severity': 'warning'
                })
        
        # Check success rate drop
        success_key = f"{platform}:success"
        if success_key in self.windows:
            window = self.windows[success_key]
            if len(window) >= 20:
                first_half = sum(1 for m in list(window)[:10] if m['value'] > 0)
                second_half = sum(1 for m in list(window)[10:20] if m['value'] > 0)
                
                if first_half > 0 and second_half / first_half < self.thresholds['success_rate_drop']:
                    anomalies.append({
                        'type': 'success_rate_drop',
                        'platform': platform,
                        'first_half': first_half,
                        'second_half': second_half,
                        'severity': 'critical'
                    })
        
        return anomalies
    
    def get_platform_health(self, platform: str) -> Dict:
        """Get health score for platform"""
        checks = ['error', 'response_time', 'success']
        scores = []
        
        for check in checks:
            key = f"{platform}:{check}"
            if key in self.windows and len(self.windows[key]) > 0:
                recent = list(self.windows[key])[-10:]
                if check == 'error':
                    score = 1 - np.mean([m['value'] for m in recent])
                elif check == 'response_time':
                    times = [m['value'] for m in recent]
                    score = max(0, 1 - (np.mean(times) / 10))  # Normalize to 10s
                else:
                    score = np.mean([m['value'] for m in recent])
                
                scores.append(score)
        
        overall = np.mean(scores) if scores else 0.5
        
        return {
            'platform': platform,
            'health_score': overall,
            'status': 'healthy' if overall > 0.8 else 'degraded' if overall > 0.5 else 'critical',
            'metrics_tracked': len(scores)
        }