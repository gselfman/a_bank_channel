import json
import secrets
import urllib.error
import urllib.request


class RemoteError(RuntimeError):
    def __init__(self, code, retry_after=0, uncertain=False, not_modified=False):
        super().__init__('Remote request failed (HTTP %s)' % code)
        self.code, self.retry_after, self.uncertain = code, retry_after, uncertain
        self.not_modified = not_modified


def request(url, payload=None, headers=None, timeout=60, raw=False):
    headers = dict(headers or {})
    if isinstance(payload, dict):
        payload = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            content = res.read(30 * 1024 * 1024 + 1)
            if len(content) > 30 * 1024 * 1024:
                raise RemoteError('oversize', uncertain=payload is not None)
            return content if raw else json.loads(content)
    except urllib.error.HTTPError as exc:
        delay = 0
        not_modified = False
        try:
            body = json.loads(exc.read(16384))
            delay = int(body.get('parameters', {}).get('retry_after', 0))
            not_modified = body.get('description', '').startswith('Bad Request: message is not modified')
        except (ValueError, TypeError):
            pass
        raise RemoteError(exc.code, delay, exc.code >= 500, not_modified) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        # Never print an exception containing a token-bearing Telegram URL.
        raise RemoteError('network', uncertain=payload is not None) from None


def multipart(fields, name, path):
    boundary = 'abank' + secrets.token_hex(16)
    chunks = []
    for key, value in fields.items():
        chunks.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, key, value)).encode())
    chunks.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="cover.png"\r\nContent-Type: image/png\r\n\r\n' % (boundary, name)).encode())
    chunks.extend([path.read_bytes(), ('\r\n--%s--\r\n' % boundary).encode()])
    return b''.join(chunks), {'Content-Type': 'multipart/form-data; boundary=' + boundary}
