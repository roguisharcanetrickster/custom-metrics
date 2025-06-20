import os
import time
import json
import uuid
import logging
import schedule
import requests
from datetime import datetime
from google.cloud import bigquery
from google.oauth2 import service_account

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SITE_ID_FILE = "/etc/network-monitor/site_id"
BIGQUERY_PROJECT = os.getenv("BIGQUERY_PROJECT", "your-project-id")
BIGQUERY_DATASET = os.getenv("BIGQUERY_DATASET", "internet_monitoring1")
PING_TABLE = os.getenv("PING_TABLE", "ping")
SPEED_TABLE = os.getenv("SPEED_TABLE", "speed")

# Ping metrics to collect every 5 minutes
PING_METRICS = {
    "google_up": 'up{job="ping", instance="http://www.google.com/"}',
    "apple_up": 'up{job="ping", instance="https://www.apple.com/"}',
    "github_up": 'up{job="ping", instance="https://github.com/"}',
    "pihole_up": 'up{job="pihole", instance="pihole-exporter:9617"}',
    "node_up": 'up{job="node", instance="nodeexp:9100"}',
    "speedtest_up": 'up{job="speedtest", instance="speedtest:9798"}',
    "http_latency": 'probe_http_duration_seconds{job="ping", phase="connect"}',
    "http_samples": "scrape_samples_scraped{job='ping'}",
    "http_time": "scrape_duration_seconds{job='ping'}",
    "http_content_length": 'probe_http_uncompressed_body_length{job="ping"}',
    "http_duration": 'probe_duration_seconds{job="ping"}'
}

# Speedtest metrics to collect when available
SPEED_METRICS = {
    "download_mbps": 'speedtest_download_bits_per_second{job="speedtest"}',
    "upload_mbps": 'speedtest_upload_bits_per_second{job="speedtest"}',
    "ping_ms": 'speedtest_ping_latency_milliseconds{job="speedtest"}',
    "jitter_ms": 'speedtest_jitter_latency_milliseconds{job="speedtest"}'
}

class NetworkMonitor:
    def __init__(self):
        LOCATION = os.getenv("LOCATION", "unknown")
        self.site_id = self._get_or_create_site_id()
        self.bigquery_client = self._init_bigquery_client()
        self.location = LOCATION
        
    def _get_or_create_site_id(self):
        """Get existing site ID or create a new one."""
        if os.path.exists(SITE_ID_FILE):
            with open(SITE_ID_FILE, 'r') as f:
                return f.read().strip()
        else:
            site_id = str(uuid.uuid4())
            os.makedirs(os.path.dirname(SITE_ID_FILE), exist_ok=True)
            with open(SITE_ID_FILE, 'w') as f:
                f.write(site_id)
            return site_id

    def _init_bigquery_client(self):
        """Initialize BigQuery client with service account credentials."""
        try:
            return bigquery.Client()
        except Exception as e:
            logger.error(f"Failed to initialize BigQuery client: {e}")
            raise

    def _query_prometheus(self, query):
        """Query Prometheus for metrics."""
        try:
            prometheus_url = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
            response = requests.get(
                f"{prometheus_url}/api/v1/query",
                params={"query": query}
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to query Prometheus: {e}")
            return None

    def _insert_ping_metrics(self, metrics_data):
        """Insert ping metrics into BigQuery."""
        try:
            row = {
                'timestamp': datetime.utcnow().isoformat(),
                'site_id': self.site_id,
                'location': self.location,
                **metrics_data
            }
            
            table_id = f"{BIGQUERY_PROJECT}.{BIGQUERY_DATASET}.{PING_TABLE}"
            errors = self.bigquery_client.insert_rows_json(table_id, [row])
            
            if errors:
                logger.error(f"Failed to insert ping metrics: {errors}")
            else:
                logger.info("Successfully inserted ping metrics")
                
        except Exception as e:
            logger.error(f"Error inserting ping metrics: {e}")

    def _insert_speed_metrics(self, metrics_data):
        """Insert speedtest metrics into BigQuery."""
        try:
            row = {
                'timestamp': datetime.utcnow().isoformat(),
                'site_id': self.site_id,
                'location': self.location,
                **metrics_data
            }
            
            table_id = f"{BIGQUERY_PROJECT}.{BIGQUERY_DATASET}.{SPEED_TABLE}"
            errors = self.bigquery_client.insert_rows_json(table_id, [row])
            
            if errors:
                logger.error(f"Failed to insert speed metrics: {errors}")
            else:
                logger.info("Successfully inserted speed metrics")
                
        except Exception as e:
            logger.error(f"Error inserting speed metrics: {e}")

    def collect_ping_metrics(self):
        """Collect and store ping metrics."""
        logger.info("Collecting ping metrics...")
        metrics_data = {}
        
        for metric_name, query in PING_METRICS.items():
            result = self._query_prometheus(query)
            if result and 'data' in result and 'result' in result['data']:
                for r in result['data']['result']:
                    value = float(r['value'][1])
                    # Convert bits to Mbps for speed metrics
                    if metric_name in ['download_mbps', 'upload_mbps']:
                        value = value / 1_000_000
                    metrics_data[metric_name] = value
        
        if metrics_data:
            self._insert_ping_metrics(metrics_data)

    def collect_speed_metrics(self):
        """Collect and store speedtest metrics if available."""
        logger.info("Checking for speedtest metrics...")
        metrics_data = {}
        
        # First check if speedtest is up
        speedtest_up = self._query_prometheus(SPEED_METRICS['download_mbps'])
        if not speedtest_up or 'data' not in speedtest_up or not speedtest_up['data']['result']:
            logger.info("No speedtest data available")
            return
        
        for metric_name, query in SPEED_METRICS.items():
            result = self._query_prometheus(query)
            if result and 'data' in result and 'result' in result['data']:
                for r in result['data']['result']:
                    value = float(r['value'][1])
                    # Convert bits to Mbps for speed metrics
                    if metric_name in ['download_mbps', 'upload_mbps']:
                        value = value / 1_000_000
                    metrics_data[metric_name] = value
        
        if metrics_data:
            self._insert_speed_metrics(metrics_data)

def main():
    monitor = NetworkMonitor()
    
    # Schedule ping metrics collection every 5 minutes
    schedule.every(5).minutes.do(monitor.collect_ping_metrics)
    
    # Schedule speedtest metrics collection every 5 minutes
    # (it will only insert data if new speedtest results are available)
    schedule.every(5).minutes.do(monitor.collect_speed_metrics)
    
    # Run initial collection
    monitor.collect_ping_metrics()
    monitor.collect_speed_metrics()
    
    # Keep the script running
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
