import redis

# Connect to the Redis server
r = redis.Redis(host='localhost', port=6379, db=0)

# Fetch all keys
keys = r.keys('*')

print(f"Total keys found: {len(keys)}")
# Retrieve and print the TTL for each key
for key in keys:
    ttl = r.ttl(key)
    print(f"Key: {key.decode('utf-8')}, TTL: {ttl}")