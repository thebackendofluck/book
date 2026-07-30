# Debug ELK Stack - Local Log Analysis Environment

Local ELK (Elasticsearch, Logstash, Kibana) stack for debugging platform environments that lack centralized logging. Used during incident response to pull logs from production servers and analyze them locally with full-text search and visualization.

## How It Works

1. Run `grab_logs.sh` to SSH into platform servers and download log files
2. Run `docker-compose up -d` to start the local ELK stack
3. Navigate to http://localhost:5601/ to access Kibana
4. Credentials are configured in `kibana/config/kibana.yml`

## Architecture

```
grab_logs.sh              # Downloads logs from remote servers via SSH
docker-compose.yml        # ELK stack orchestration
elasticsearch/            # Elasticsearch config + Dockerfile
logstash/
  config/logstash.yml     # Logstash settings
  pipeline/logstash.conf  # Log parsing pipeline (JSON codec)
kibana/                   # Kibana config + Dockerfile
logs/                     # Downloaded logs mounted into Logstash
```

## Usage

```bash
# 1. Update grab_logs.sh with your username and target servers
# 2. Download logs from platform servers
./grab_logs.sh

# 3. Start the ELK stack
docker-compose up -d

# 4. Open Kibana
open http://localhost:5601/
```

## Key Patterns for Book Readers

1. **Offline incident analysis**: Pull logs to local machine when centralized logging is unavailable
2. **JSON log codec**: Platform logs are structured JSON, parsed natively by Logstash
3. **Archived log handling**: Automatically decompresses .gz archived logs
4. **Lightweight local stack**: 256MB JVM heap per service for laptop-friendly operation

## Chapter Reference

Chapter 35: Incident Management
