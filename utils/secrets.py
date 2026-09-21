# utils/secrets.py
import os
import json
from typing import Dict, Optional
import hvac  # HashiCorp Vault client
import boto3  # AWS Secrets Manager

class SecretManager:
    """Secure secrets management"""
    
    def __init__(self, backend: str = 'env'):
        self.backend = backend
        self._cache: Dict[str, str] = {}
        
        if backend == 'vault':
            self.vault_client = hvac.Client(url=os.getenv('VAULT_ADDR'))
        elif backend == 'aws':
            self.secrets_client = boto3.client('secretsmanager')
    
    async def get(self, key: str) -> Optional[str]:
        """Get secret with caching"""
        
        if key in self._cache:
            return self._cache[key]
        
        value = await self._fetch(key)
        if value:
            self._cache[key] = value
        return value
    
    async def _fetch(self, key: str) -> Optional[str]:
        """Fetch from backend"""
        
        if self.backend == 'env':
            return os.getenv(key)
        
        elif self.backend == 'vault':
            response = self.vault_client.secrets.kv.v2.read_secret_version(
                path=key
            )
            return response['data']['data']['value']
        
        elif self.backend == 'aws':
            response = self.secrets_client.get_secret_value(SecretId=key)
            return response['SecretString']
        
        return None
    
    async def get_json(self, key: str) -> Dict:
        """Get and parse JSON secret"""
        value = await self.get(key)
        return json.loads(value) if value else {}
    
    def invalidate_cache(self, key: str = None):
        """Clear cache"""
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

# Config with hot reloading
class DynamicConfig:
    """Configuration that updates without restart"""
    
    def __init__(self, db):
        self.db = db
        self._config: Dict = {}
        self._last_update: Optional[datetime] = None
        self._refresh_interval = 60  # seconds
        
    async def get(self, key: str, default=None):
        """Get config value"""
        await self._maybe_refresh()
        return self._config.get(key, default)
    
    async def _maybe_refresh(self):
        """Refresh if stale"""
        if (self._last_update and 
            (datetime.now() - self._last_update).seconds < self._refresh_interval):
            return
        
        # Fetch from DB
        rows = await self.db.fetch("SELECT key, value, type FROM config")
        
        for row in rows:
            if row['type'] == 'json':
                self._config[row['key']] = json.loads(row['value'])
            elif row['type'] == 'int':
                self._config[row['key']] = int(row['value'])
            elif row['type'] == 'float':
                self._config[row['key']] = float(row['value'])
            elif row['type'] == 'bool':
                self._config[row['key']] = row['value'].lower() == 'true'
            else:
                self._config[row['key']] = row['value']
        
        self._last_update = datetime.now()
    
    async def update(self, key: str, value, type: str = 'string'):
        """Update config"""
        await self.db.execute("""
            INSERT INTO config (key, value, type, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                type = EXCLUDED.type,
                updated_at = NOW()
        """, key, json.dumps(value) if type == 'json' else str(value), type)
        
        self._config[key] = value