import os
from redis import Redis
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
redis=None
def _redis():
    global redis
    if redis is None: redis=Redis.from_url(os.environ['REDIS_URL'])
    return redis
class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        if request.url.path!='/health':
            key='http:'+str(request.client.host)+':'+str(int(__import__('time').time())//60)
            client=_redis()
            n=client.incr(key)
            if n==1: client.expire(key,65)
            if n>240: return JSONResponse({'detail':'每分钟最多240次，请稍后重试'},status_code=429)
        return await call_next(request)
