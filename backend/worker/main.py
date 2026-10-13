import os

from redis import Redis
from rq import Queue, Worker

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
conn = Redis.from_url(redis_url)

if __name__ == "__main__":
    queues = [Queue("amiga-extraction", connection=conn)]
    worker = Worker(queues, connection=conn)
    worker.work()
