"""Test liveness of hosts"""
import socket
import web

DATABASE_ACCESS=None

def myWebError(msg):
    return web.HTTPError(msg, {"Content-type": "text/plain"}, msg+'\n\n')

def lan(name=None, service='services/%s', ip=None, port=80, timeout=5):
    if not name:
        raise myWebError("401 Required argument name missing")
    if not ip:
        ip = name
    alive = True
    try:
        s = socket.create_connection((ip, int(port)), timeout)
    except socket.error:
        alive = False
    if service:
        if '%' in service:
            service = service % name
        if not DATABASE_ACCESS: 
            msg = "502 plugin did not have DATABASE_ACCESS set"
            raise web.HTTPError(msg, {"Content-type": "text/plain"}, msg+'\n\n')
        try:
            oldValue = DATABASE_ACCESS.get_key(service, 'text/plain', None)
        except web.HTTPError:
            web.ctx.status = "200 OK"
            oldValue = 'rabarber'
        if oldValue != repr(alive):
            xpAlive = 'true' if alive else ''
            try:
                rv = DATABASE_ACCESS.put_key(service + '/alive', 'text/plain', None, xpAlive, 'text/plain', replace=True)
            except web.HTTPError:
                raise myWebError("501 Failed to store into %s" % service)
            if alive:
                # If the service is alive we delete any error message and we also reset the "ignore errors" indicator
                try:
                    DATABASE_ACCESS.delete_key(service + '/errorMessage')
                except web.HTTPError:
                    pass
                try:
                    DATABASE_ACCESS.delete_key(service + '/ignoreErrorUntil')
                except web.HTTPError:
                    pass
            else:
                # If the service is not alive we set an error message
                DATABASE_ACCESS.put_key(service + '/errorMessage', 'text/plain', None, "%s is not available" % name, 'text/plain', replace=True)
    return repr(alive)
