# utils/disaster_recovery.py
import asyncio
import json
from datetime import datetime, timedelta
from typing import List, Dict

class DisasterRecovery:
    """Handle failures gracefully"""
    
    def __init__(self, db, s3_client):
        self.db = db
        self.s3 = s3_client
        self.checkpoint_interval = 3600  # Hourly
        
    async def create_checkpoint(self):
        """Snapshot entire system state"""
        
        timestamp = datetime.now().isoformat()
        
        # Export critical tables
        tables = ['campaigns', 'posts', 'accounts', 'content_pool', 'optimization_logs']
        
        for table in tables:
            data = await self.db.fetch(f"SELECT * FROM {table}")
            
            # Upload to S3
            await self.s3.put_object(
                Bucket='affiliate-backups',
                Key=f"checkpoints/{timestamp}/{table}.json",
                Body=json.dumps([dict(row) for row in data])
            )
        
        # Store checkpoint metadata
        await self.db.execute("""
            INSERT INTO checkpoints (timestamp, tables, status)
            VALUES ($1, $2, 'complete')
        """, timestamp, tables)
        
        return timestamp
    
    async def restore_from_checkpoint(self, timestamp: str):
        """Restore system to checkpoint"""
        
        for table in ['campaigns', 'posts', 'accounts', 'content_pool']:
            # Download from S3
            response = await self.s3.get_object(
                Bucket='affiliate-backups',
                Key=f"checkpoints/{timestamp}/{table}.json"
            )
            data = json.loads(await response['Body'].read())
            
            # Restore to DB (truncate + insert)
            await self.db.execute(f"TRUNCATE TABLE {table} CASCADE")
            
            if data:
                columns = list(data[0].keys())
                placeholders = ','.join(f'${i+1}' for i in range(len(columns)))
                
                for row in data:
                    values = [row[c] for c in columns]
                    await self.db.execute(f"""
                        INSERT INTO {table} ({','.join(columns)})
                        VALUES ({placeholders})
                    """, *values)
        
        return {'restored': timestamp, 'tables': len(data)}
    
    async def emergency_pivot(self, platform: str):
        """When platform completely fails"""
        
        # 1. Pause all campaigns on that platform
        await self.db.execute("""
            UPDATE campaigns 
            SET status = 'emergency_paused',
                emergency_reason = $2
            WHERE platform = $1 AND status = 'active'
        """, platform, f"Emergency pivot from {platform}")
        
        # 2. Get campaigns that need migration
        campaigns = await self.db.fetch("""
            SELECT * FROM campaigns 
            WHERE platform = $1 AND status = 'emergency_paused'
        """, platform)
        
        # 3. Migrate to backup platforms
        migration_map = {
            'reddit': 'twitter',
            'twitter': 'telegram',
            'telegram': 'reddit'
        }
        
        backup_platform = migration_map.get(platform, 'reddit')
        
        for campaign in campaigns:
            # Clone to backup platform
            await self.db.execute("""
                INSERT INTO campaigns 
                (config, platform, status, migrated_from)
                VALUES ($1, $2, 'active', $3)
            """, 
                {**campaign['config'], 'platform': backup_platform},
                backup_platform,
                campaign['id']
            )
        
        return {
            'paused': len(campaigns),
            'migrated_to': backup_platform,
            'status': 'emergency_pivot_complete'
        }