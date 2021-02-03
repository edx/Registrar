"""
gunicorn configuration file: http://docs.gunicorn.org/en/develop/configure.html
"""


preload_app = True
timeout = 300
bind = "0.0.0.0:18734"
workers = 2


def pre_request(worker, req):
    """
    Called just before a worker processes the request.
    Logs a request to the worker.

    Args:
        worker (Worker): The worker to process the request
        req (Request): The request to be processed

    Returns:
        None
    """
    worker.log.info("%s %s" % (req.method, req.path))


def close_all_caches():
    """
    Closes the cache so that newly forked workers cannot accidentally
    share the socket with the processes they were forked from.
    Prevents a race condition in which one worker could get a cache response
    intended for another worker.

    Args:
        None

    Returns:
        None
    """
    from django.conf import settings  # pylint: disable=import-outside-toplevel
    from django.core import cache as django_cache  # pylint: disable=import-outside-toplevel
    if hasattr(django_cache, 'caches'):
        get_cache = django_cache.caches.__getitem__
    else:
        get_cache = django_cache.get_cache  # pylint: disable=no-member
    for cache_name in settings.CACHES:
        cache = get_cache(cache_name)
        if hasattr(cache, 'close'):
            cache.close()

    # The 1.4 global default cache object needs to be closed also: 1.4
    # doesn't ensure you get the same object when requesting the same
    # cache. The global default is a separate Python object from the cache
    # you get with get_cache("default"), so it will have its own connection
    # that needs to be closed.
    cache = django_cache.cache
    if hasattr(cache, 'close'):
        cache.close()


def post_fork(server, worker):  # pylint: disable=unused-argument
    """
    Called just after a worker has been forked.

    Args:
        server (Arbiter): The arbiter that maintains worker's processes
        worker (Worker): The worker that was forked

    Returns:
        None
    """
    close_all_caches()
