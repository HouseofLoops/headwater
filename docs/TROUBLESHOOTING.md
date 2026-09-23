# Troubleshooting Guide

This guide helps you diagnose and resolve common issues with the Headwater API.

## Table of Contents

- [Quick Start](#quick-start)
- [Common Issues](#common-issues)
- [API Issues](#api-issues)
- [Authentication Issues](#authentication-issues)
- [Performance Issues](#performance-issues)
- [Database Issues](#database-issues)
- [Cache Issues](#cache-issues)
- [Container Issues](#container-issues)
- [Network Issues](#network-issues)
- [Monitoring and Logs](#monitoring-and-logs)
- [Debugging Tools](#debugging-tools)
- [Getting Help](#getting-help)

## Quick Start

### Health Check

First, verify the API is running and accessible:

```bash
# Check if the API is responding
curl -f http://localhost:8000/health

# Check with detailed output
curl -v http://localhost:8000/health

# Check API documentation endpoint
curl -f http://localhost:8000/docs
```

### Basic Diagnostics

```bash
# Check if required services are running
docker-compose ps

# Check application logs
docker-compose logs api

# Check Redis connectivity
docker-compose exec redis redis-cli ping

# Check PostgreSQL connectivity
docker-compose exec db psql -U user -d headwater -c "SELECT version();"
```

## Common Issues

### Application Won't Start

**Symptoms:**
- Container exits immediately
- Port 8000 is not accessible
- Logs show startup errors

**Solutions:**

1. **Check Environment Variables**

   ```bash
   # Verify all required environment variables are set
   echo $DATABASE_URL
   echo $REDIS_URL
   echo $GOOGLE_API_KEY
   echo $SECRET_KEY
   ```

2. **Check Dependencies**

   ```bash
   # Install missing Python dependencies
   pip install -r requirements.txt

   # Check for missing system dependencies
   apt-get update && apt-get install -y build-essential
   ```

3. **Check Database Connection**

   ```python
   # Test database connection in Python
   import asyncpg
   import asyncio


   async def test_db():
       try:
           conn = await asyncpg.connect(os.getenv("DATABASE_URL"))
           await conn.close()
           print("Database connection successful")
       except Exception as e:
           print(f"Database connection failed: {e}")


   asyncio.run(test_db())
   ```

4. **Check Redis Connection**

   ```python
   # Test Redis connection
   import redis

   try:
       r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
       r.ping()
       print("Redis connection successful")
   except Exception as e:
       print(f"Redis connection failed: {e}")
   ```

### High Memory Usage

**Symptoms:**
- Application consumes excessive memory
- Out of memory errors
- Slow performance

**Solutions:**

1. **Check Memory Leaks**

   ```python
   # Use memory profiler to identify leaks
   from memory_profiler import profile
   import tracemalloc

   tracemalloc.start()

   # Your code here

   snapshot = tracemalloc.take_snapshot()
   top_stats = snapshot.statistics("lineno")

   for stat in top_stats[:10]:
       print(stat)
   ```

2. **Optimize Cache Settings**

   ```python
   # Adjust cache settings
   CACHE_TTL = 1800  # Reduce TTL from 1 hour to 30 minutes
   CACHE_MAX_MEMORY = 256mb  # Reduce max memory
   ```

3. **Check for Large Data Structures**

   ```python
   # Monitor object sizes
   import sys


   def get_size(obj, seen=None):
       size = sys.getsizeof(obj)
       if seen is None:
           seen = set()

       obj_id = id(obj)
       if obj_id in seen:
           return 0

       seen.add(obj_id)
       if isinstance(obj, dict):
           size += sum([get_size(v, seen) for v in obj.values()])
           size += sum([get_size(k, seen) for k in obj.keys()])
       elif hasattr(obj, "__dict__"):
           size += get_size(obj.__dict__, seen)
       elif hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes, bytearray)):
           try:
               size += sum([get_size(i, seen) for i in obj])
           except TypeError:
               pass
       return size


   large_object = {}  # Your potentially large object
   print(f"Object size: {get_size(large_object)} bytes")
   ```

### Slow Response Times

**Symptoms:**
- API requests take longer than expected
- Timeout errors
- Poor user experience

**Solutions:**

1. **Check External API Response Times**

   ```python
   # Monitor external API calls
   import time
   import httpx


   async def monitor_external_api():
       start_time = time.time()

       # Your code here
       await asyncio.sleep(1)

       # Make HTTP request
       async with httpx.AsyncClient() as session:
           async with session.get("https://httpbin.org/delay/1") as response:
               await response.text()

       end_time = time.time()
       print(f"External API response time: {end_time - start_time:.2f} seconds")
   ```

2. **Profile Database Queries**

   ```sql
   -- Check slow queries
   SELECT query, mean_time, calls, total_time
   FROM pg_stat_statements
   ORDER BY mean_time DESC
   LIMIT 10;
   ```

3. **Check Cache Hit Rate**

   ```python
   # Monitor cache performance
   cache_hits = 0
   cache_misses = 0


   def get_cache_stats():
       total_requests = cache_hits + cache_misses
       hit_rate = cache_hits / total_requests if total_requests > 0 else 0
       print(f"Cache hit rate: {hit_rate:.2%}")
       print(f"Hits: {cache_hits}, Misses: {cache_misses}")
   ```

### Invalid API Responses

**Symptoms:**
- Unexpected response format
- Missing data fields
- Incorrect data types

**Debugging Steps:**

1. **Check Request Parameters**

   ```python
   # Validate request parameters
   from pydantic import BaseModel, ValidationError


   class NewsSearchRequest(BaseModel):
       q: str
       country: str = "US"
       max_results: int = 10


   try:
       request = NewsSearchRequest(q="test", country="US", max_results=5)
       print("Request validation successful")
   except ValidationError as e:
       print(f"Request validation failed: {e}")
   ```

2. **Inspect Raw API Responses**

   ```python
   # Log raw responses for debugging
   import logging
   import json

   logging.basicConfig(level=logging.DEBUG)


   async def debug_api_response(url: str):
       async with httpx.AsyncClient() as client:
           response = await client.get(url)
           print(f"Status: {response.status_code}")
           print(f"Headers: {dict(response.headers)}")
           print(f"Content: {response.text[:500]}...")

           try:
               data = response.json()
               print(f"Parsed JSON: {json.dumps(data, indent=2)[:500]}...")
           except:
               print("Response is not valid JSON")
   ```

3. **Test with Different Parameters**

   ```bash
   # Test with minimal parameters
   curl "http://localhost:8000/api/v1/google-news/search?q=test"

   # Test with all parameters
   curl "http://localhost:8000/api/v1/google-news/search?q=test&country=US&max_results=5&sort_by=relevance"

   # Test with invalid parameters
   curl "http://localhost:8000/api/v1/google-news/search?q=&country=INVALID"
   ```

### Rate Limiting Issues

**Symptoms:**
- 429 Too Many Requests errors
- Requests being blocked
- Inconsistent API behavior

**Solutions:**

1. **Check Rate Limits**

   ```python
   # Monitor rate limiting
   from collections import defaultdict
   import time


   class RateLimiter:
       def __init__(self, requests_per_minute: int = 60):
           self.requests_per_minute = requests_per_minute
           self.requests = defaultdict(list)

       def is_allowed(self, client_id: str) -> bool:
           now = time.time()
           client_requests = self.requests[client_id]

           # Remove old requests
           client_requests[:] = [req for req in client_requests if now - req < 60]

           if len(client_requests) >= self.requests_per_minute:
               return False

           client_requests.append(now)
           return True


   limiter = RateLimiter()
   ```

2. **Implement Exponential Backoff**

   ```python
   # Retry with exponential backoff
   import asyncio
   import random


   async def retry_with_backoff(func, max_retries: int = 3):
       for attempt in range(max_retries):
           try:
               return await func()
           except Exception as e:
               if "429" in str(e) or "rate limit" in str(e).lower():
                   wait_time = (2**attempt) + random.uniform(0, 1)
                   print(f"Rate limited, waiting {wait_time:.2f} seconds")
                   await asyncio.sleep(wait_time)
               else:
                   raise
       raise Exception("Max retries exceeded")
   ```

3. **Check API Key Limits**

   ```python
   # Monitor API key usage
   api_key_usage = defaultdict(int)


   def check_api_key_limits(api_key: str) -> bool:
       usage = api_key_usage[api_key]
       daily_limit = 10000  # Adjust based on your limits

       if usage >= daily_limit:
           print(f"API key {api_key} has exceeded daily limit")
           return False

       api_key_usage[api_key] += 1
       return True
   ```

### High Memory Usage

**Symptoms:**
- Application consumes excessive memory
- Out of memory errors
- Slow performance

**Solutions:**

1. **Check Memory Leaks**
   ```python
   # Use memory profiler to identify leaks
   from memory_profiler import profile
   import tracemalloc

   tracemalloc.start()

   # Your code here

   snapshot = tracemalloc.take_snapshot()
   top_stats = snapshot.statistics("lineno")

   for stat in top_stats[:10]:
       print(stat)
   ```

2. **Optimize Cache Settings**
   ```python
   # Adjust cache settings
   CACHE_TTL = 1800  # Reduce TTL from 1 hour to 30 minutes
   CACHE_MAX_MEMORY = 256mb  # Reduce max memory
   ```

3. **Check for Large Data Structures**
   ```python
   # Monitor object sizes
   import sys


   def get_size(obj, seen=None):
       size = sys.getsizeof(obj)
       if seen is None:
           seen = set()

       obj_id = id(obj)
       if obj_id in seen:
           return 0

       seen.add(obj_id)
       if isinstance(obj, dict):
           size += sum([get_size(v, seen) for v in obj.values()])
           size += sum([get_size(k, seen) for k in obj.keys()])
       elif hasattr(obj, "__dict__"):
           size += get_size(obj.__dict__, seen)
       elif hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes, bytearray)):
           try:
               size += sum([get_size(i, seen) for i in obj])
           except TypeError:
               pass
       return size


   large_object = {}  # Your potentially large object
   print(f"Object size: {get_size(large_object)} bytes")
   ```

### Slow Response Times

**Symptoms:**
- API requests take longer than expected
- Timeout errors
- Poor user experience

**Solutions:**

1. **Check External API Response Times**
   ```python
   # Monitor external API calls
   import time
   import httpx


   async def monitor_external_api():
       start_time = time.time()

       async with httpx.AsyncClient() as client:
           response = await client.get("https://www.google.com/complete/search?q=test")

       end_time = time.time()
       print(f"External API response time: {end_time - start_time:.2f} seconds")
   ```

2. **Profile Database Queries**
   ```sql
   -- Enable query logging in PostgreSQL
   ALTER DATABASE headwater SET log_statement = 'all';
   ALTER DATABASE headwater SET log_duration = on;

   -- Check slow queries
   SELECT query, mean_time, calls, total_time
   FROM pg_stat_statements
   ORDER BY mean_time DESC
   LIMIT 10;
   ```

3. **Check Cache Hit Rate**
   ```python
   # Monitor cache performance
   cache_hits = 0
   cache_misses = 0


   def get_cache_stats():
       total_requests = cache_hits + cache_misses
       hit_rate = cache_hits / total_requests if total_requests > 0 else 0
       print(f"Cache hit rate: {hit_rate:.2%}")
       print(f"Total requests: {total_requests}")
   ```

## API Issues

### Invalid API Responses

**Symptoms:**
- Unexpected response format
- Missing data fields
- Incorrect data types

**Debugging Steps:**

1. **Check Request Parameters**
   ```python
   # Validate request parameters
   from pydantic import BaseModel, ValidationError


   class NewsSearchRequest(BaseModel):
       q: str
       country: str = "US"
       max_results: int = 10


   try:
       request = NewsSearchRequest(q="test", country="US", max_results=5)
       print("Request validation successful")
   except ValidationError as e:
       print(f"Request validation failed: {e}")
   ```

2. **Inspect Raw API Responses**
   ```python
   # Log raw responses for debugging
   import logging
   import json

   logging.basicConfig(level=logging.DEBUG)


   async def debug_api_response(url: str):
       async with httpx.AsyncClient() as client:
           response = await client.get(url)
           print(f"Status: {response.status_code}")
           print(f"Headers: {dict(response.headers)}")
           print(f"Content: {response.text[:500]}...")

           try:
               data = response.json()
               print(f"Parsed JSON: {json.dumps(data, indent=2)[:500]}...")
           except:
               print("Response is not valid JSON")
   ```

3. **Test with Different Parameters**
   ```bash
   # Test with minimal parameters
   curl "http://localhost:8000/api/v1/google-news/search?q=test"

   # Test with all parameters
   curl "http://localhost:8000/api/v1/google-news/search?q=test&country=US&max_results=5&sort_by=relevance"

   # Test with invalid parameters
   curl "http://localhost:8000/api/v1/google-news/search?q=&country=INVALID"
   ```

## Authentication Issues

### API Key Authentication Problems

**Symptoms:**

- 401 Unauthorized errors
- Authentication failed messages
- Access denied to endpoints

**Solutions:**

1. **Verify API Key Format**

   ```python
   # Validate API key format
   import re


   def validate_api_key(api_key: str) -> bool:
       # Adjust pattern based on your API key format
       pattern = r"^[A-Za-z0-9]{32,64}$"
       return bool(re.match(pattern, api_key))


   api_key = "your_api_key_here"
   if not validate_api_key(api_key):
       print("Invalid API key format")
   ```

2. **Check API Key in Headers**

   ```bash
   # Test with API key in header
   curl -H "x-api-key: your_api_key" http://localhost:8000/api/v1/google-news/search?q=test

   # Test with different header names
   curl -H "X-API-Key: your_api_key" http://localhost:8000/api/v1/google-news/search?q=test
   curl -H "X-API-Key: your_api_key" http://localhost:8000/api/v1/google-news/search?q=test
   ```

3. **Verify API Key Storage**

   ```python
   # Check if API key exists in database
   import asyncpg


   async def check_api_key_exists(api_key: str):
       conn = await asyncpg.connect(os.getenv("DATABASE_URL"))

       result = await conn.fetchval(
           """
           SELECT EXISTS(
               SELECT 1 FROM api_keys
               WHERE key = $1 AND active = true
           )
       """,
           api_key,
       )

       await conn.close()
       return result


   exists = await check_api_key_exists("your_api_key")
   print(f"API key exists: {exists}")
   ```

### API Key Expiration Issues

**Symptoms:**

- API key was working but now fails
- Token expired messages
- Authentication works intermittently

**Solutions:**

1. **Check API Key Expiration**

   ```python
   # Verify API key expiration
   from datetime import datetime


   async def check_api_key_expiration(api_key: str):
       conn = await asyncpg.connect(os.getenv("DATABASE_URL"))

       result = await conn.fetchrow(
           """
           SELECT expires_at, active
           FROM api_keys
           WHERE key = $1
       """,
           api_key,
       )

       await conn.close()

       if not result:
           return "API key not found"

       if not result["active"]:
           return "API key is inactive"

       if result["expires_at"] and result["expires_at"] < datetime.utcnow():
           return f"API key expired on {result['expires_at']}"

       return "API key is valid"
   ```

2. **Renew API Key**

   ```python
   # Generate new API key
   import secrets


   def generate_new_api_key(length: int = 32) -> str:
       return secrets.token_urlsafe(length)


   new_key = generate_new_api_key()
   print(f"New API key: {new_key}")
   ```

## Performance Issues

### Slow Database Queries

**Symptoms:**
- Database queries take too long
- Application response times increase
- Database CPU usage is high

**Solutions:**

1. **Analyze Query Performance**
   ```sql
   -- Find slow queries
   SELECT
       query,
       mean_time,
       calls,
       total_time,
       rows
   FROM pg_stat_statements
   ORDER BY mean_time DESC
   LIMIT 10;

   -- Check for missing indexes
   SELECT
       schemaname,
       tablename,
       attname,
       n_distinct,
       correlation
   FROM pg_stats
   WHERE schemaname = 'public'
   ORDER BY n_distinct DESC;
   ```

2. **Add Database Indexes**
   ```sql
   -- Add indexes for common query patterns
   CREATE INDEX CONCURRENTLY idx_news_search_vector ON news_articles USING gin(search_vector);
   CREATE INDEX CONCURRENTLY idx_news_published ON news_articles(published DESC);
   CREATE INDEX CONCURRENTLY idx_news_country ON news_articles(country);
   CREATE INDEX CONCURRENTLY idx_api_keys_key ON api_keys(key);
   CREATE INDEX CONCURRENTLY idx_api_keys_active_expires ON api_keys(active, expires_at);
   ```

3. **Optimize Query Structure**
   ```python
   # Before: Inefficient query
   async def get_news_slow(query: str, limit: int = 10):
       async with db_session() as session:
           result = await session.execute(
               """
               SELECT * FROM news_articles
               WHERE title ILIKE :query
               ORDER BY published DESC
               LIMIT :limit
           """,
               {"query": f"%{query}%", "limit": limit},
           )
           return result.fetchall()


   # After: Optimized query with full-text search
   async def get_news_optimized(query: str, limit: int = 10):
       async with db_session() as session:
           result = await session.execute(
               """
               SELECT id, title, link, source, published, snippet,
                      ts_rank_cd(search_vector, plainto_tsquery(:query)) as rank
               FROM news_articles
               WHERE search_vector @@ plainto_tsquery(:query)
                 AND published >= NOW() - INTERVAL '30 days'
               ORDER BY rank DESC, published DESC
               LIMIT :limit
           """,
               {"query": query, "limit": limit},
           )
           return result.fetchall()
   ```

### High CPU Usage

**Symptoms:**
- CPU usage consistently high
- Application becomes unresponsive
- Other services affected

**Solutions:**

1. **Profile CPU Usage**
   ```python
   # Profile CPU-intensive functions
   import cProfile
   import pstats


   def profile_cpu_usage():
       profiler = cProfile.Profile()
       profiler.enable()

       # Your CPU-intensive code here

       profiler.disable()
       stats = pstats.Stats(profiler)
       stats.sort_stats("cumulative").print_stats(20)


   profile_cpu_usage()
   ```

2. **Optimize CPU-Intensive Operations**
   ```python
   # Use async processing for I/O operations
   import asyncio
   import concurrent.futures


   async def process_batch_async(items):
       # Process items concurrently
       with concurrent.futures.ThreadPoolExecutor() as executor:
           loop = asyncio.get_event_loop()
           tasks = [loop.run_in_executor(executor, process_item, item) for item in items]
           return await asyncio.gather(*tasks)


   # Use multiprocessing for CPU-bound tasks
   from multiprocessing import Pool
   import os


   def cpu_intensive_task(data):
       # CPU-intensive processing
       return processed_data


   def process_with_multiprocessing(items):
       num_processes = os.cpu_count()
       with Pool(processes=num_processes) as pool:
           results = pool.map(cpu_intensive_task, items)
       return results
   ```

3. **Implement Caching for Expensive Operations**
   ```python
   # Cache expensive computations
   from functools import lru_cache
   import asyncio


   @lru_cache(maxsize=1000)
   def expensive_computation(param: str) -> str:
       # Expensive computation here
       return result


   # For async functions
   cache = {}


   async def cached_async_function(key: str):
       if key in cache:
           return cache[key]

       result = await expensive_async_operation(key)
       cache[key] = result
       return result
   ```

## Database Issues

### Connection Pool Exhaustion

**Symptoms:**
- Database connection errors
- "Too many connections" errors
- Application hangs when accessing database

**Solutions:**

1. **Check Connection Pool Settings**
   ```python
   # Configure connection pool
   from sqlalchemy.ext.asyncio import create_async_engine

   engine = create_async_engine(
       DATABASE_URL,
       pool_size=10,  # Maximum number of connections
       max_overflow=20,  # Additional connections beyond pool_size
       pool_timeout=30,  # Timeout for getting connection from pool
       pool_recycle=3600,  # Recycle connections after 1 hour
       echo=False,
   )
   ```

2. **Monitor Connection Usage**
   ```python
   # Track connection usage
   import psutil
   import asyncpg


   async def monitor_db_connections():
       conn = await asyncpg.connect(DATABASE_URL)

       # Get current connection count
       result = await conn.fetchval("""
           SELECT count(*) FROM pg_stat_activity
           WHERE datname = current_database()
       """)

       print(f"Active connections: {result}")

       # Get connection pool stats
       pool_stats = await conn.fetch("""
           SELECT state, count(*) as count
           FROM pg_stat_activity
           WHERE datname = current_database()
           GROUP BY state
       """)

       for row in pool_stats:
           print(f"{row['state']}: {row['count']}")

       await conn.close()
   ```

3. **Implement Connection Retry Logic**
   ```python
   # Retry database connections
   import asyncio
   from sqlalchemy.exc import OperationalError


   async def execute_with_retry(query, max_retries: int = 3):
       for attempt in range(max_retries):
           try:
               async with db_session() as session:
                   result = await session.execute(query)
                   return result
           except OperationalError as e:
               if attempt == max_retries - 1:
                   raise
               wait_time = 2**attempt
               print(f"Database connection failed, retrying in {wait_time}s")
               await asyncio.sleep(wait_time)
   ```

### Database Lock Contention

**Symptoms:**
- Queries hang or take very long
- Deadlock errors
- Performance degrades under load

**Solutions:**

1. **Identify Lock Conflicts**
   ```sql
   -- Check for blocking queries
   SELECT
       blocked_locks.pid AS blocked_pid,
       blocked_activity.usename AS blocked_user,
       blocking_locks.pid AS blocking_pid,
       blocking_activity.usename AS blocking_user,
       blocked_activity.query AS blocked_query,
       blocking_activity.query AS blocking_query
   FROM pg_locks blocked_locks
   JOIN pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
   JOIN pg_locks blocking_locks
       ON blocking_locks.locktype = blocked_locks.locktype
       AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
       AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
       AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
       AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
       AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
       AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
       AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
       AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
       AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
       AND blocking_locks.pid != blocked_locks.pid
   JOIN pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
   WHERE NOT blocked_locks.granted;
   ```

2. **Optimize Transaction Scope**
   ```python
   # Before: Long-running transaction
   async def problematic_transaction():
       async with db_session() as session:
           # Multiple operations in one transaction
           await session.execute("UPDATE users SET last_login = NOW() WHERE id = 1")
           await asyncio.sleep(1)  # Some processing
           await session.execute("INSERT INTO logs VALUES (...)")
           await session.commit()


   # After: Shorter transactions
   async def optimized_transaction():
       # Update in separate transaction
       async with db_session() as session:
           await session.execute("UPDATE users SET last_login = NOW() WHERE id = 1")
           await session.commit()

       # Some processing
       await asyncio.sleep(1)

       # Insert in separate transaction
       async with db_session() as session:
           await session.execute("INSERT INTO logs VALUES (...)")
           await session.commit()
   ```

3. **Use Appropriate Isolation Levels**
   ```python
   # Set appropriate isolation level
   from sqlalchemy import IsolationLevel


   async def execute_with_isolation():
       async with db_session() as session:
           # Use READ COMMITTED for most cases
           await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))

           # Your queries here
           await session.commit()
   ```

## Cache Issues

### Cache Misses

**Symptoms:**
- High cache miss rate
- Slow response times
- Increased load on external APIs

**Solutions:**

1. **Monitor Cache Performance**
   ```python
   # Track cache hit/miss rates
   cache_stats = {"hits": 0, "misses": 0}


   async def get_with_stats(key: str):
       if key in cache:
           cache_stats["hits"] += 1
           return cache[key]

       cache_stats["misses"] += 1
       value = await fetch_from_source(key)
       cache[key] = value
       return value


   def print_cache_stats():
       total = cache_stats["hits"] + cache_stats["misses"]
       hit_rate = cache_stats["hits"] / total if total > 0 else 0
       print(f"Cache hit rate: {hit_rate:.2%}")
       print(f"Hits: {cache_stats['hits']}, Misses: {cache_stats['misses']}")
   ```

2. **Optimize Cache Keys**
   ```python
   # Generate consistent cache keys
   def generate_cache_key(endpoint: str, **params) -> str:
       # Sort parameters for consistency
       sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
       return f"{endpoint}:{sorted_params}"


   # Normalize parameters
   def normalize_params(**params):
       normalized = {}
       for key, value in params.items():
           if isinstance(value, str):
               normalized[key] = value.lower().strip()
           else:
               normalized[key] = value
       return normalized
   ```

3. **Implement Cache Warming**
   ```python
   # Warm up cache with popular queries
   async def warmup_cache():
       popular_queries = ["artificial intelligence", "machine learning", "data science", "python programming"]

       for query in popular_queries:
           # Pre-populate cache
           await get_news_cached(query, max_results=5)
           await get_autocomplete_cached(query)
   ```

### Redis Connection Issues

**Symptoms:**
- Redis connection errors
- Cache operations fail
- Application falls back to direct API calls

**Solutions:**

1. **Test Redis Connectivity**
   ```python
   # Test Redis connection
   import redis
   import asyncio


   async def test_redis_connection():
       try:
           r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))

           # Test basic operations
           await r.ping()
           await r.set("test_key", "test_value")
           value = await r.get("test_key")
           await r.delete("test_key")

           print("Redis connection successful")
           return True

       except Exception as e:
           print(f"Redis connection failed: {e}")
           return False
   ```

2. **Configure Redis Connection Pool**
   ```python
   # Configure Redis connection pool
   import redis.asyncio as redis

   redis_pool = redis.ConnectionPool.from_url(
       REDIS_URL,
       max_connections=20,
       decode_responses=True,
       retry_on_timeout=True,
       socket_timeout=5,
       socket_connect_timeout=5,
       health_check_interval=30,
   )

   redis_client = redis.Redis(connection_pool=redis_pool)
   ```

3. **Implement Redis Failover**
   ```python
   # Redis with failover
   import redis.asyncio as redis
   from redis.asyncio.sentinel import Sentinel


   async def create_redis_with_failover():
       sentinel = Sentinel([("redis-sentinel-1", 26379), ("redis-sentinel-2", 26379)], socket_timeout=0.1)

       master = sentinel.master_for("mymaster", socket_timeout=0.1)
       return master
   ```

## Container Issues

### Container Won't Start

**Symptoms:**
- Container exits immediately after start
- Docker logs show errors
- Health checks fail

**Solutions:**

1. **Check Container Logs**
   ```bash
   # View container logs
   docker-compose logs api

   # Follow logs in real-time
   docker-compose logs -f api

   # Check specific time range
   docker-compose logs --since "1h" api
   ```

2. **Debug Container Entrypoint**
   ```bash
   # Run container with shell
   docker run -it --entrypoint /bin/bash headwater/api:latest

   # Check if Python is available
   python --version

   # Test imports
   python -c "import fastapi; print('FastAPI imported successfully')"
   ```

3. **Check Environment Variables**
   ```bash
   # List all environment variables in container
   docker exec -it headwater_api_1 env

   # Check specific variable
   docker exec -it headwater_api_1 echo $DATABASE_URL
   ```

### Container Resource Issues

**Symptoms:**
- Container is killed by OOM killer
- CPU throttling
- Slow performance

**Solutions:**

1. **Monitor Container Resources**
   ```bash
   # Check container resource usage
   docker stats headwater_api_1

   # Check container limits
   docker inspect headwater_api_1 | grep -A 10 "Limits"
   ```

2. **Adjust Resource Limits**
   ```yaml
   # docker-compose.yml
   version: '3.8'
   services:
     api:
       image: headwater/api:latest
       deploy:
         resources:
           limits:
             cpus: '1.0'
             memory: 1G
           reservations:
             cpus: '0.5'
             memory: 512M
       environment:
         - GOMEMLIMIT=1073741824  # 1GB in bytes for Go-style memory limiting
   ```

3. **Optimize Container Configuration**
   ```dockerfile
   # Optimized Dockerfile
   FROM python:3.14-slim-trixie

   # Install only necessary system dependencies
   RUN apt-get update && apt-get install -y \
       curl \
       && rm -rf /var/lib/apt/lists/*

   # Create non-root user
   RUN useradd --create-home --shell /bin/bash app

   # Set working directory
   WORKDIR /app

   # Copy and install Python dependencies
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt

   # Copy application code
   COPY --chown=app:app . .

   # Switch to non-root user
   USER app

   # Expose port
   EXPOSE 8000

   # Health check
   HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
       CMD curl -f http://localhost:8000/health || exit 1

   # Run application
   CMD ["python", "main.py"]
   ```

## Network Issues

### Connection Timeouts

**Symptoms:**
- Requests to external APIs timeout
- Intermittent connection failures
- Slow network performance

**Solutions:**

1. **Configure Network Timeouts**
   ```python
   # Configure HTTP client timeouts
   import httpx

   timeout = httpx.Timeout(
       connect=10.0,  # Connection timeout
       read=30.0,  # Read timeout
       write=10.0,  # Write timeout
       pool=5.0,  # Pool timeout
   )

   client = httpx.AsyncClient(timeout=timeout)
   ```

2. **Implement Retry Logic**
   ```python
   # Retry failed requests
   import asyncio
   from tenacity import retry, stop_after_attempt, wait_exponential


   @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
   async def make_request_with_retry(url: str):
       async with httpx.AsyncClient() as client:
           response = await client.get(url)
           response.raise_for_status()
           return response.json()
   ```

3. **Check Network Connectivity**
   ```bash
   # Test network connectivity
   curl -v https://www.google.com/complete/search?q=test

   # Check DNS resolution
   nslookup www.google.com

   # Test with different DNS servers
   curl --dns-servers 8.8.8.8,8.8.4.4 https://www.google.com

   # Check network routes
   traceroute www.google.com
   ```

### SSL/TLS Issues

**Symptoms:**
- SSL certificate errors
- TLS handshake failures
- Connection refused errors

**Solutions:**

1. **Check SSL Certificates**
   ```python
   # Verify SSL certificates
   import ssl
   import socket


   def check_ssl_certificate(hostname: str, port: int = 443):
       context = ssl.create_default_context()
       with socket.create_connection((hostname, port)) as sock:
           with context.wrap_socket(sock, server_hostname=hostname) as ssock:
               cert = ssock.getpeercert()
               print(f"Certificate for {hostname}:")
               print(f"  Issued to: {cert['subject']}")
               print(f"  Issued by: {cert['issuer']}")
               print(f"  Valid from: {cert['notBefore']}")
               print(f"  Valid until: {cert['notAfter']}")
   ```

2. **Configure SSL Context**
   ```python
   # Custom SSL configuration
   import httpx

   ssl_context = ssl.create_default_context()
   ssl_context.check_hostname = True
   ssl_context.verify_mode = ssl.CERT_REQUIRED

   # Or disable SSL verification (not recommended for production)
   client = httpx.AsyncClient(verify=False)
   ```

3. **Handle SSL Errors**
   ```python
   # Handle SSL-related exceptions
   try:
       async with httpx.AsyncClient() as client:
           response = await client.get(url)
   except ssl.SSLError as e:
       print(f"SSL Error: {e}")
       # Try with different SSL configuration
   except httpx.ConnectError as e:
       print(f"Connection Error: {e}")
   ```

## Monitoring and Logs

### Application Logs

**Symptoms:**
- Missing log entries
- Incorrect log levels
- Log files growing too large

**Solutions:**

1. **Configure Logging**
   ```python
   # Configure structured logging
   import logging
   import json
   from pythonjsonlogger import jsonlogger


   class CustomJsonFormatter(jsonlogger.JsonFormatter):
       def add_fields(self, log_record, record, message_dict):
           super().add_fields(log_record, record, message_dict)
           log_record["timestamp"] = record.created
           log_record["level"] = record.levelname
           log_record["module"] = record.module
           log_record["function"] = record.funcName


   logger = logging.getLogger()
   handler = logging.StreamHandler()
   formatter = CustomJsonFormatter()
   handler.setFormatter(formatter)
   logger.addHandler(handler)
   logger.setLevel(logging.INFO)
   ```

2. **Log Key Events**
   ```python
   # Log important application events
   import logging

   logger = logging.getLogger(__name__)


   async def log_api_request(request, response_time: float):
       logger.info(
           "API request completed",
           extra={
               "method": request.method,
               "url": str(request.url),
               "status_code": response.status_code,
               "response_time": response_time,
               "user_agent": request.headers.get("user-agent"),
               "client_ip": request.client.host if request.client else None,
           },
       )


   async def log_error(error: Exception, request=None):
       logger.error(
           "Application error occurred",
           extra={
               "error_type": type(error).__name__,
               "error_message": str(error),
               "url": str(request.url) if request else None,
               "method": request.method if request else None,
           },
           exc_info=True,
       )
   ```

3. **Log Rotation**
   ```python
   # Configure log rotation
   from logging.handlers import RotatingFileHandler

   handler = RotatingFileHandler(
       "app.log",
       maxBytes=10 * 1024 * 1024,  # 10MB
       backupCount=5,
   )
   handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

   logger = logging.getLogger()
   logger.addHandler(handler)
   ```

### Performance Monitoring

**Symptoms:**
- Performance degradation over time
- Memory leaks
- CPU spikes

**Solutions:**

1. **Add Performance Metrics**
   ```python
   # Prometheus metrics
   from prometheus_client import Counter, Histogram, Gauge

   REQUEST_COUNT = Counter("http_requests_total", "Total number of HTTP requests", ["method", "endpoint", "status"])

   REQUEST_LATENCY = Histogram("http_request_duration_seconds", "HTTP request duration", ["method", "endpoint"])

   ACTIVE_CONNECTIONS = Gauge("active_connections", "Number of active connections")
   ```

2. **Monitor System Resources**
   ```python
   # System resource monitoring
   import psutil
   import time


   def monitor_system_resources():
       while True:
           cpu_percent = psutil.cpu_percent(interval=1)
           memory = psutil.virtual_memory()
           disk = psutil.disk_usage("/")

           logger.info(
               "System resources",
               extra={
                   "cpu_percent": cpu_percent,
                   "memory_percent": memory.percent,
                   "memory_used": memory.used,
                   "memory_total": memory.total,
                   "disk_percent": disk.percent,
                   "disk_free": disk.free,
               },
           )

           time.sleep(60)  # Monitor every minute
   ```

3. **Profile Memory Usage**
   ```python
   # Memory profiling
   import tracemalloc
   import gc

   tracemalloc.start()

   # Your application code here

   # Take memory snapshot
   snapshot = tracemalloc.take_snapshot()
   top_stats = snapshot.statistics("lineno")

   logger.info("Memory usage statistics:")
   for stat in top_stats[:10]:
       logger.info(f"  {stat}")

   # Force garbage collection
   gc.collect()
   ```

## Debugging Tools

### Interactive Debugging

```python
# Add debug breakpoints
import pdb


def debug_function():
    # Set breakpoint
    pdb.set_trace()

    # Your code here
    x = 1
    y = 2
    result = x + y

    return result


# Use IPython for enhanced debugging
from IPython import embed


def debug_with_ipython():
    # Your code here

    # Start IPython session
    embed()
```

### Remote Debugging

```python
# Remote debugging with debugpy
import debugpy

# Enable remote debugging
debugpy.listen(("0.0.0.0", 5678))
print("Remote debugger listening on port 5678")

# Your application code here
```

### Logging Debug Information

```python
# Debug logging for troubleshooting
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("debug.log"), logging.StreamHandler()],
)

logger = logging.getLogger(__name__)


def debug_function_call(func):
    def wrapper(*args, **kwargs):
        logger.debug(f"Calling {func.__name__} with args={args}, kwargs={kwargs}")
        try:
            result = func(*args, **kwargs)
            logger.debug(f"{func.__name__} returned {result}")
            return result
        except Exception as e:
            logger.error(f"{func.__name__} raised {type(e).__name__}: {e}")
            raise

    return wrapper


@debug_function_call
def problematic_function():
    # Your code here
    pass
```

### Database Query Debugging

```python
# Log all database queries
import logging
from sqlalchemy import event
from sqlalchemy.engine import Engine

logging.basicConfig()
logger = logging.getLogger("sqlalchemy.engine")
logger.setLevel(logging.INFO)


# Log SQL queries
@event.listens_for(Engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    logger.info(f"Executing: {statement}")
    logger.info(f"Parameters: {parameters}")


@event.listens_for(Engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    logger.info(f"Query executed successfully")
```

## Getting Help

### Community Support

1. **GitHub Issues**
   - Check existing issues for similar problems
   - Create a new issue with detailed information
   - Include error messages, logs, and reproduction steps

2. **Documentation**
   - Review the [API Reference](API_REFERENCE.md)
   - Check the [Performance Tuning Guide](PERFORMANCE_TUNING.md)
   - Read the [Security Guidelines](SECURITY_GUIDELINES.md)

3. **Debugging Checklist**
   - [ ] Verify environment variables are set correctly
   - [ ] Check database and Redis connections
   - [ ] Review application logs for error messages
   - [ ] Test API endpoints with minimal parameters
   - [ ] Monitor system resources (CPU, memory, disk)
   - [ ] Check network connectivity to external services
   - [ ] Verify API key validity and permissions
   - [ ] Test with different browsers/clients
   - [ ] Check for recent code changes that might have introduced issues
   - [ ] Review recent deployments or configuration changes

### Information to Include When Reporting Issues

When creating a bug report or seeking help, please include:

1. **Environment Information**
   ```bash
   # System information
   uname -a
   python --version
   docker --version
   docker-compose --version
   ```

2. **Application Version**
   ```bash
   # Check application version
   curl http://localhost:8000/version
   ```

3. **Error Messages and Logs**
   ```bash
   # Recent application logs
   docker-compose logs --tail=100 api

   # System logs
   journalctl -u docker -n 100
   ```

4. **Configuration**
   ```bash
   # Docker Compose configuration (redact sensitive data)
   cat docker-compose.yml

   # Environment variables (redact secrets)
   env | grep -E "(DATABASE|REDIS|API)" | head -10
   ```

5. **Reproduction Steps**
   - Exact commands used
   - Input parameters
   - Expected vs actual behavior
   - Frequency of occurrence

6. **Performance Metrics**
   - Response times
   - Error rates
   - Resource usage
   - Cache hit rates

---

This troubleshooting guide provides comprehensive solutions for common issues with the Headwater API. For additional help, please check the [GitHub Issues](https://github.com/yourusername/headwater/issues) or create a new issue with detailed information about your problem.